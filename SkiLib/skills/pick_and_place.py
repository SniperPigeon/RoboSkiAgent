"""
Pick and Place Skill

Executes a safe pick-and-place motion sequence:
  transit → pick_approach → [MoveL plunge] → pick → GRASP → [MoveL retract] →
  pick_approach → transit → place_approach → [MoveL plunge] → place → RELEASE →
  [MoveL retract] → place_approach

Design principles:
  - All parameters are plain strings (RoboDK target names) for LLM Tool compatibility.
  - Approach targets are resolved from explicit names or auto-searched by naming convention.
  - check() validates every motion segment before a single joint moves.
  - Grasp / Release primitives are optional; execution continues with a warning if absent.
"""

from __future__ import annotations

from typing import Optional, Tuple

from robodk import robolink

from SkiLib.base import (
    BaseSkill,
    ExecutionPhase,
    RobotState,
    SkillResult,
    ERROR_INVALID_PARAM,
    ERROR_IK_FAILURE,
    ERROR_COLLISION,
    require_robot_active,
)
from SkiLib.log import get_logger

logger = get_logger(__name__)

# Approach target naming conventions searched in order when no explicit name is given.
_APPROACH_SUFFIXES = ["_App", "_Approach", "_approach"]
_APPROACH_PREFIXES = ["App_"]

# Sentinel used internally so helpers can signal "no error".
_OK: Optional[SkillResult] = None


class PickAndPlace(BaseSkill):
    """
    Pick an object from pick_target and place it at place_target using a
    safe approach-retract motion sequence.

    Required primitives : MoveJ, MoveL
    Optional primitives : Grasp, Release
    """

    SKILL_DESCRIPTION = (
        "Pick an object from pick_target and place it at place_target. "
        "Uses approach targets for safe linear entry and exit. "
        "Approach targets are resolved from explicit names or auto-searched by naming convention."
    )
    REQUIRED_PRIMITIVES = ["MoveJ", "MoveL"]

    # ------------------------------------------------------------------
    # Public interface  (check / execute / try_execute — identical sigs)
    # ------------------------------------------------------------------

    def check(
        self,
        pick_target: str,
        place_target: str,
        pick_approach: str = "",
        place_approach: str = "",
        motion_type: str = "MoveJ",
        grasp_force: float = 0.0,
        grasp_width: float = 0.0,
        release_width: float = 0.0,
        skip_feasibility_check: bool = False,
    ) -> SkillResult:
        """
        Validate the full pick-and-place motion plan without moving the robot.

        Checks all four motion segments in sequence:
          1. current → pick_approach      (motion_type)
          2. pick_approach → pick_target  (MoveL)
          3. pick_target → place_approach (motion_type)
          4. place_approach → place_target (MoveL)

        Also runs Grasp.check() pre-conditions if the Grasp primitive is available.
        Release.check() is intentionally skipped at planning time because held_item
        is always None before execution begins.

        Args:
            pick_target:          RoboDK target name for the grasp point.
            place_target:         RoboDK target name for the release point.
            pick_approach:        Approach target name for pick side; auto-searched if empty.
            place_approach:       Approach target name for place side; auto-searched if empty.
            motion_type:          Transit motion type — "MoveJ" (default) or "MoveL".
            grasp_force:          Gripping force in N passed to Grasp (0 = gripper default).
            grasp_width:          Jaw width at grasp point in mm (0 = gripper default).
            release_width:        Jaw opening width in mm after release (0 = fully open).
            skip_feasibility_check: DEBUG ONLY — skip all segment feasibility checks (IK,
                                  singularity, and collision) and only verify that all named
                                  targets exist in the RoboDK station.
                                  Do NOT use in production; path feasibility is not guaranteed.

        Returns:
            SkillResult with success=True if all segments are feasible.
        """
        err = self._validate_motion_type(motion_type)
        if err:
            return err

        ctx = self._get_context()
        if isinstance(ctx, SkillResult):
            return ctx

        pick_item, err = self._resolve_item(ctx, pick_target, "pick_target")
        if err:
            return err
        place_item, err = self._resolve_item(ctx, place_target, "place_target")
        if err:
            return err
        pick_app_item, err = self._resolve_approach(ctx, pick_item, pick_approach, "pick_approach")
        if err:
            return err
        place_app_item, err = self._resolve_approach(ctx, place_item, place_approach, "place_approach")
        if err:
            return err

        # Validate Grasp parameters and tool availability before any motion check.
        # Release.check() is skipped here: it verifies held_item is not None, which is
        # always False at planning time, so calling it would produce a false-negative.
        if "Grasp" in self.primitives:
            grasp_check = self.primitives["Grasp"].check(grasp_force, grasp_width)
            if not grasp_check.success:
                grasp_check.message = f"[Grasp pre-check] {grasp_check.message}"
                return grasp_check

        if skip_feasibility_check:
            logger.warning(
                "check() — collision/IK checks BYPASSED (skip_feasibility_check=True). "
                "All four targets resolved OK but path feasibility is NOT guaranteed. "
                "pick='%s', place='%s'",
                pick_target, place_target,
            )
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.PLANNING,
                message=(
                    f"[DEBUG] Targets resolved; collision/IK checks skipped. "
                    f"pick '{pick_target}' → place '{place_target}'."
                ),
            )

        robot = ctx.robot
        RDK   = ctx.RDK
        RDK.setCollisionActive(True)
        try:
            j_current    = list(robot.Joints())
            j_pick_app   = list(pick_app_item.Joints())
            j_pick_tgt   = list(pick_item.Joints())
            j_place_app  = list(place_app_item.Joints())

            # Segment 1: current → pick_approach
            err = self._check_segment(
                robot, j_current, pick_app_item, motion_type,
                f"current → pick_approach '{pick_app_item.Name()}'",
            )
            if err:
                return err

            # Segment 2: pick_approach → pick_target  (always MoveL)
            err = self._check_segment(
                robot, j_pick_app, pick_item, "MoveL",
                f"pick_approach '{pick_app_item.Name()}' → pick_target '{pick_target}'",
            )
            if err:
                return err

            # Segment 3: pick_target → place_approach
            err = self._check_segment(
                robot, j_pick_tgt, place_app_item, motion_type,
                f"pick_target '{pick_target}' → place_approach '{place_app_item.Name()}'",
            )
            if err:
                return err

            # Segment 4: place_approach → place_target  (always MoveL)
            err = self._check_segment(
                robot, j_place_app, place_item, "MoveL",
                f"place_approach '{place_app_item.Name()}' → place_target '{place_target}'",
            )
            if err:
                return err

        finally:
            RDK.setCollisionActive(False)

        logger.info(
            "check() passed for pick '%s' → place '%s' (motion_type=%s)",
            pick_target, place_target, motion_type,
        )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=(
                f"All motion segments validated: pick '{pick_target}' → place '{place_target}'."
            ),
        )

    @require_robot_active
    def execute(
        self,
        pick_target: str,
        place_target: str,
        pick_approach: str = "",
        place_approach: str = "",
        motion_type: str = "MoveJ",
        grasp_force: float = 0.0,
        grasp_width: float = 0.0,
        release_width: float = 0.0,
        skip_feasibility_check: bool = False,
    ) -> SkillResult:
        """
        Execute the pick-and-place motion sequence.

        Motion sequence:
          1. Transit to pick_approach          (motion_type)
          2. MoveL plunge to pick_target       (always linear)
          3. Grasp                             (optional — skipped with warning if unavailable)
          4. MoveL retract to pick_approach    (always linear)
          5. Transit to place_approach         (motion_type)
          6. MoveL plunge to place_target      (always linear)
          7. Release                           (optional — skipped with warning if unavailable)
          8. MoveL retract to place_approach   (always linear)

        Any step failure returns immediately with that step's SkillResult.

        Args:
            pick_target:          RoboDK target name for the grasp point.
            place_target:         RoboDK target name for the release point.
            pick_approach:        Approach target name for pick side; auto-searched if empty.
            place_approach:       Approach target name for place side; auto-searched if empty.
            motion_type:          Transit motion type — "MoveJ" (default) or "MoveL".
            grasp_force:          Gripping force in N passed to Grasp (0 = gripper default).
            grasp_width:          Jaw width at grasp point in mm (0 = gripper default).
            release_width:        Jaw opening width in mm after release (0 = fully open).
            skip_feasibility_check: Accepted for signature consistency with check(); has no
                                  effect here since execute() does not perform feasibility checks.

        Returns:
            SkillResult with success=True on full completion.
        """
        err = self._validate_motion_type(motion_type)
        if err:
            return err

        ctx = self._get_context()
        if isinstance(ctx, SkillResult):
            return ctx

        pick_item, err = self._resolve_item(ctx, pick_target, "pick_target")
        if err:
            return err
        place_item, err = self._resolve_item(ctx, place_target, "place_target")
        if err:
            return err
        pick_app_item, err = self._resolve_approach(ctx, pick_item, pick_approach, "pick_approach")
        if err:
            return err
        place_app_item, err = self._resolve_approach(ctx, place_item, place_approach, "place_approach")
        if err:
            return err

        if skip_feasibility_check:
            logger.debug(
                "execute() — skip_feasibility_check=True has no effect here; "
                "feasibility checks only apply in check()."
            )

        move_transit = self.primitives["MoveJ" if motion_type == "MoveJ" else "MoveL"]
        move_linear  = self.primitives["MoveL"]

        # --- Step 1: transit to pick approach ---
        logger.info("Step 1/8 — transit to pick_approach '%s'", pick_app_item.Name())
        result = move_transit.execute(pick_app_item)
        if not result.success:
            result.message = f"[Step 1 transit → pick_approach] {result.message}"
            return result

        # --- Step 2: linear plunge to pick target ---
        logger.info("Step 2/8 — MoveL plunge to pick_target '%s'", pick_target)
        result = move_linear.execute(pick_item)
        if not result.success:
            result.message = f"[Step 2 approach → pick_target] {result.message}"
            return result

        # --- Step 3: grasp ---
        if "Grasp" in self.primitives:
            logger.info("Step 3/8 — Grasp (force=%.1f N, width=%.1f mm)", grasp_force, grasp_width)
            result = self.primitives["Grasp"].execute(grasp_force, grasp_width)
            if not result.success:
                result.message = f"[Step 3 grasp] {result.message}"
                return result
        else:
            logger.warning(
                "Step 3/8 — No 'Grasp' primitive registered; skipping grasp at '%s'",
                pick_target,
            )

        # --- Step 4: linear retract to pick approach ---
        logger.info("Step 4/8 — MoveL retract to pick_approach '%s'", pick_app_item.Name())
        result = move_linear.execute(pick_app_item)
        if not result.success:
            result.message = f"[Step 4 retract from pick] {result.message}"
            return result

        # --- Step 5: transit to place approach ---
        logger.info("Step 5/8 — transit to place_approach '%s'", place_app_item.Name())
        result = move_transit.execute(place_app_item)
        if not result.success:
            result.message = f"[Step 5 transit → place_approach] {result.message}"
            return result

        # --- Step 6: linear plunge to place target ---
        logger.info("Step 6/8 — MoveL plunge to place_target '%s'", place_target)
        result = move_linear.execute(place_item)
        if not result.success:
            result.message = f"[Step 6 approach → place_target] {result.message}"
            return result

        # --- Step 7: release ---
        if "Release" in self.primitives:
            logger.info("Step 7/8 — Release (width=%.1f mm)", release_width)
            result = self.primitives["Release"].execute(release_width)
            if not result.success:
                result.message = f"[Step 7 release] {result.message}"
                return result
        else:
            logger.warning(
                "Step 7/8 — No 'Release' primitive registered; skipping release at '%s'",
                place_target,
            )

        # --- Step 8: linear retract to place approach ---
        logger.info("Step 8/8 — MoveL retract to place_approach '%s'", place_app_item.Name())
        result = move_linear.execute(place_app_item)
        if not result.success:
            result.message = f"[Step 8 retract from place] {result.message}"
            return result

        try:
            final_state = RobotState(
                joints=list(ctx.robot.Joints()),
                pose=ctx.robot.Pose(),
            )
        except Exception:
            final_state = RobotState()

        logger.info(
            "PickAndPlace completed: '%s' → '%s'", pick_target, place_target
        )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            robot_state=final_state,
            message=f"PickAndPlace completed: '{pick_target}' → '{place_target}'.",
            data={"pick_target": pick_target, "place_target": place_target},
        )

    @require_robot_active
    def try_execute(
        self,
        pick_target: str,
        place_target: str,
        pick_approach: str = "",
        place_approach: str = "",
        motion_type: str = "MoveJ",
        grasp_force: float = 0.0,
        grasp_width: float = 0.0,
        release_width: float = 0.0,
        skip_feasibility_check: bool = False,
    ) -> SkillResult:
        """
        Check feasibility then execute if valid.  Returns the check failure
        directly if pre-validation fails.

        Args:
            pick_target:          RoboDK target name for the grasp point.
            place_target:         RoboDK target name for the release point.
            pick_approach:        Approach target name for pick side; auto-searched if empty.
            place_approach:       Approach target name for place side; auto-searched if empty.
            motion_type:          Transit motion type — "MoveJ" (default) or "MoveL".
            grasp_force:          Gripping force in N passed to Grasp (0 = gripper default).
            grasp_width:          Jaw width at grasp point in mm (0 = gripper default).
            release_width:        Jaw opening width in mm after release (0 = fully open).
            skip_feasibility_check: DEBUG ONLY — forwarded to check(); skips all segment
                                  feasibility checks (IK, singularity, collision).
                                  See check() for full details.
        """
        check_result = self.check(
            pick_target, place_target, pick_approach, place_approach, motion_type,
            grasp_force, grasp_width, release_width, skip_feasibility_check,
        )
        if not check_result.success:
            return check_result
        return self.execute(  # type: ignore[return-value]
            pick_target, place_target, pick_approach, place_approach, motion_type,
            grasp_force, grasp_width, release_width, skip_feasibility_check,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_context(self):
        """Return the live RobotContext or a SkillResult failure."""
        from SkiLib.robotcontext import RobotContext
        ctx = RobotContext.instance()
        if ctx is None:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="RobotContext is not initialized. Call RobotContext() before using skills.",
                suggestion="Ensure RobotContext() is constructed at application startup.",
            )
        return ctx

    def _validate_motion_type(self, motion_type: str) -> Optional[SkillResult]:
        """Return a failure SkillResult if motion_type is not 'MoveJ' or 'MoveL'."""
        if motion_type not in ("MoveJ", "MoveL"):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=(
                    f"Invalid motion_type '{motion_type}'. "
                    "Accepted values: 'MoveJ', 'MoveL'."
                ),
                suggestion="Pass motion_type='MoveJ' (default) or motion_type='MoveL'.",
            )
        return _OK

    def _resolve_item(self, ctx, name: str, label: str) -> Tuple:
        """
        Look up a RoboDK target by name.

        Returns:
            (item, None) on success
            (None, SkillResult) if the target does not exist
        """
        item = ctx.RDK.Item(name, robolink.ITEM_TYPE_TARGET)
        if not item.Valid():
            return None, SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=f"RoboDK target '{name}' not found (expected for {label}).",
                suggestion=(
                    f"Verify that a target named '{name}' exists in the RoboDK station."
                ),
            )
        return item, _OK

    def _resolve_approach(self, ctx, target_item, explicit_name: str, label: str) -> Tuple:
        """
        Resolve an approach target.

        Priority:
          1. explicit_name if provided — must exist or returns an error.
          2. Auto-search by naming convention suffixes then prefixes.

        Naming conventions tried (in order):
          {target_name}_App  →  {target_name}_Approach  →  {target_name}_approach  →  App_{target_name}

        Returns:
            (item, None) on success
            (None, SkillResult) if no matching target is found
        """
        target_name = target_item.Name()

        if explicit_name:
            item = ctx.RDK.Item(explicit_name, robolink.ITEM_TYPE_TARGET)
            if item.Valid():
                logger.debug(
                    "Using explicit approach target '%s' for %s", explicit_name, label
                )
                return item, _OK
            return None, SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=(
                    f"Explicit approach target '{explicit_name}' not found in RoboDK station "
                    f"(expected for {label})."
                ),
                suggestion=(
                    f"Create a target named '{explicit_name}' in RoboDK, "
                    "or omit the argument to enable naming-convention auto-search."
                ),
            )

        candidates = (
            [f"{target_name}{s}" for s in _APPROACH_SUFFIXES]
            + [f"{p}{target_name}" for p in _APPROACH_PREFIXES]
        )
        for candidate in candidates:
            item = ctx.RDK.Item(candidate, robolink.ITEM_TYPE_TARGET)
            if item.Valid():
                logger.info(
                    "Auto-resolved approach target '%s' for %s", candidate, label
                )
                return item, _OK

        return None, SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_INVALID_PARAM,
            message=(
                f"No approach target found for '{target_name}' ({label}). "
                f"Searched: {candidates}."
            ),
            suggestion=(
                f"Create an approach target in RoboDK using one of these names: "
                + ", ".join(f"'{c}'" for c in candidates)
                + f". Or pass the name explicitly via the {label} parameter."
            ),
        )

    def _check_segment(
        self,
        robot,
        j_start: list,
        target_item,
        motion: str,
        label: str,
    ) -> Optional[SkillResult]:
        """
        Check one motion segment for IK feasibility and collisions.
        Assumes the caller has already enabled collision detection.

        Args:
            robot:       RoboDK robot Item.
            j_start:     Start configuration as a list of joint angles (degrees).
            target_item: RoboDK target Item (destination).
            motion:      "MoveL" or "MoveJ".
            label:       Human-readable segment label for error messages.

        Returns:
            None if the segment is feasible, or a SkillResult failure otherwise.
        """
        if motion == "MoveL":
            # Express target pose in the robot base frame for MoveL_Test
            target_pose = robot.PoseFrame().inv() * target_item.Pose()
            code = robot.MoveL_Test(j_start, target_pose)
            if code == 0:
                return _OK
            if code == -2:
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.PLANNING,
                    error_type=ERROR_IK_FAILURE,
                    message=f"[{label}] Target pose is outside robot reachable workspace.",
                    suggestion=(
                        "Verify the target coordinates and orientation. "
                        "Consider adjusting the approach direction or robot configuration."
                    ),
                )
            if code == -1:
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.PLANNING,
                    error_type=ERROR_IK_FAILURE,
                    message=(
                        f"[{label}] Linear path passes through a singularity "
                        "or workspace boundary."
                    ),
                    suggestion=(
                        "The robot cannot maintain a straight Cartesian path to the target. "
                        "Use MoveJ for the transit, or approach from a different direction."
                    ),
                )
            # code > 0: collision count
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_COLLISION,
                message=f"[{label}] Linear path would cause {code} collision(s).",
                data={"collision_count": code},
                suggestion=(
                    "Check the collision map to identify colliding pairs "
                    "and adjust the approach direction or target placement."
                ),
            )

        else:  # MoveJ
            j_target = list(target_item.Joints())
            code = robot.MoveJ_Test(j_start, j_target)
            if code == 0:
                return _OK
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_COLLISION,
                message=f"[{label}] Joint path would cause {code} collision(s).",
                data={"collision_count": code},
                suggestion=(
                    "Check the collision map and adjust the path, "
                    "robot configuration, or target placement."
                ),
            )
