"""
Pick and Place Skill - Platform-agnostic implementation

Architecture:
- NO RoboDK imports (platform-agnostic)
- All symbol IDs (str) resolved to RoboDK Items via RobotContext
- Dependencies injected via primitives registry
- Pure composition of primitives
- Returns SkillResult throughout

Execution sequence:
    1. initial_motion to pick_approach  (initial_motion, default MoveL)
    2. MoveL  to pick_target            (linear precise approach)
    3. Grasp  item                      (close gripper)
    4. MoveL  to pick_approach          (linear depart, same as approach)
    5. transit_motion to place_approach (transit_motion, default MoveL)
    6. MoveL  to place_target           (linear precise approach)
    7. Release item                     (open gripper)
    8. MoveL  to place_approach         (linear depart)
"""

from typing import Dict, Optional, Tuple
from SkiLib.base import BaseSkill, SkillResult, ExecutionPhase
from SkiLib.robotcontext import RobotContext
from SkiLib.log import get_logger

logger = get_logger(__name__)

_VALID_TRANSIT_MOTIONS = ("MoveJ", "MoveL")


_CTX_NOT_INITIALIZED = SkillResult(
    success=False,
    execution_phase=ExecutionPhase.VALIDATION,
    error_type="CONTEXT_NOT_INITIALIZED",
    message="RobotContext has not been initialized. Call RobotContext.initialize() before using skills.",
)


def _resolve(name: str, ctx: RobotContext) -> Tuple[Optional[object], Optional[SkillResult]]:
    """Resolve a symbol name to a RoboDK Item. Returns (item, None) on success or (None, error) on failure."""
    obj = ctx.RDK.Item(name)
    if not obj.Valid():
        return None, SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type="ITEM_NOT_FOUND",
            message=f"Item '{name}' not found in the RoboDK station.",
            suggestion="Verify the name matches exactly what is shown in RoboDK's item tree.",
        )
    return obj, None


class PickAndPlace(BaseSkill):
    """
    Pick an object from pick_target and place it at place_target using a
    safe approach-retract motion sequence.

    All position/item arguments are RoboDK symbol names (strings).
    Symbols are resolved to RoboDK Items internally via RobotContext.

    Sequence (execute):
        1. initial_motion → Home_position  (initial_motion: MoveJ or MoveL, default MoveL)
        2. MoveL          → pick_approach  (linear approach to grasp point)
        3. MoveL          → pick_target    (linear precise approach to grasp point)
        4. Grasp            item
        5. MoveL          → pick_approach  (linear depart with workpiece)
        6. transit_motion → place_approach (transit_motion: MoveJ or MoveL, default MoveL)
        7. MoveL          → place_target   (linear precise approach to place point)
        8. Release          item
        9. MoveL          → place_approach (linear depart, empty gripper)
        10 MoveL          → Home_position  (return to home)
    """

    SKILL_DESCRIPTION   = "Pick an object from pick_target and place it at place_target."
    SKILL_CATEGORY      = "manipulation"
    REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL', 'Grasp', 'Release']

    def __init__(self, primitives: Dict):
        super().__init__(primitives)

    # ------------------------------------------------------------------
    # check
    # ------------------------------------------------------------------

    def check(
        self,
        item: str,
        home_position: str,
        pick_approach: str,
        pick_target: str,
        place_approach: str,
        place_target: str,
        transit_motion: str = "MoveL",
        initial_motion: str = "MoveL",
    ) -> SkillResult:
        """
        Pre-flight feasibility check.

        Args:
            item:            RoboDK name of the workpiece to grasp/release.
            home_position:   Target name for the initial and final home position.
            pick_approach:   Target name for the approach/depart point near pick.
            pick_target:     Target name for the linear-move precise grasp point.
            place_approach:  Target name for the transit destination / depart point near place.
            place_target:    Target name for the linear-move precise place point.
            transit_motion:  Motion type for the pick_approach→place_approach segment.
                             Must be "MoveL" (default) or "MoveJ".
            initial_motion:  Motion type for the initial move to pick_approach.
                             Must be "MoveL" (default) or "MoveJ".

        Returns:
            SkillResult — success=False with first failing check on failure.

        Note:
            All motion checks use the robot's current position as start. Full sequential
            path simulation is not possible at planning time; this check covers reachability
            and item validity only.
        """
        # 1. Validate motion type parameters
        if initial_motion not in _VALID_TRANSIT_MOTIONS:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type="INVALID_PARAM",
                message=f"initial_motion must be 'MoveJ' or 'MoveL', got '{initial_motion}'.",
            )
        if transit_motion not in _VALID_TRANSIT_MOTIONS:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type="INVALID_PARAM",
                message=f"transit_motion must be 'MoveJ' or 'MoveL', got '{transit_motion}'.",
            )

        # 2. Resolve all symbols
        ctx = RobotContext.instance()
        if ctx is None:
            return _CTX_NOT_INITIALIZED
        item_obj, err = _resolve(item, ctx)
        if err:
            return err
        home_position_obj, err = _resolve(home_position, ctx)
        if err:
            return err
        pick_approach_obj, err = _resolve(pick_approach, ctx)
        if err:
            return err
        pick_target_obj, err = _resolve(pick_target, ctx)
        if err:
            return err
        place_approach_obj, err = _resolve(place_approach, ctx)
        if err:
            return err
        place_target_obj, err = _resolve(place_target, ctx)
        if err:
            return err

        # 3. Check pick_approach reachable via initial_motion
        result = self.primitives[initial_motion].check(target=home_position_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"home_position '{home_position}' not reachable via {initial_motion}: {result.message}",
                suggestion=result.suggestion,
            )
        
        result = self.primitives['MoveL'].check(target=pick_approach_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"pick_approach '{pick_approach}' not reachable via {initial_motion}: {result.message}",
                suggestion=result.suggestion,
            )

        # 4. Check pick_target reachable via MoveL
        result = self.primitives['MoveL'].check(target=pick_target_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"pick_target '{pick_target}' not reachable via MoveL: {result.message}",
                suggestion=result.suggestion,
            )

        # 5. Check Grasp preconditions
        result = self.primitives['Grasp'].check(expected_item=item_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"Grasp check failed for item '{item}': {result.message}",
                suggestion=result.suggestion,
            )

        # 6. Check place_approach reachable via transit_motion
        result = self.primitives[transit_motion].check(target=place_approach_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"place_approach '{place_approach}' not reachable via {transit_motion}: {result.message}",
                suggestion=result.suggestion,
            )

        # 7. Check place_target reachable via MoveL
        result = self.primitives['MoveL'].check(target=place_target_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"place_target '{place_target}' not reachable via MoveL: {result.message}",
                suggestion=result.suggestion,
            )
        # 8. Check home_position preconditions
        result = self.primitives['MoveL'].check(target=home_position_obj)
        if not result.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=result.error_type,
                message=f"Back to home_position '{home_position}' not reachable via MoveL: {result.message}",
                suggestion=result.suggestion,
            )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message="Pick and place pre-flight check passed.",
        )

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------

    def execute(
        self,
        item: str,
        home_position: str,
        pick_approach: str,
        pick_target: str,
        place_approach: str,
        place_target: str,
        transit_motion: str = "MoveL",
        initial_motion: str = "MoveL",
    ) -> SkillResult:
        """
        Execute pick and place.

        Args:
            item:            RoboDK name of the workpiece.
            home_position:   Home position for this part move.
            pick_approach:   Approach/depart point near the pick location.
            pick_target:     Precise grasp point (MoveL).
            place_approach:  Approach/depart point near the place location.
            place_target:    Precise place point (MoveL).
            transit_motion:  "MoveL" (default) or "MoveJ" for the with-workpiece transit.
            initial_motion:  "MoveL" (default) or "MoveJ" for the initial move to pick_approach.

        Returns:
            SkillResult — fails fast on first primitive failure.
        """
        # Validate motion parameters first (fast-fail before any motion)
        if initial_motion not in _VALID_TRANSIT_MOTIONS:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type="INVALID_PARAM",
                message=f"initial_motion must be 'MoveJ' or 'MoveL', got '{initial_motion}'.",
            )
        if transit_motion not in _VALID_TRANSIT_MOTIONS:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type="INVALID_PARAM",
                message=f"transit_motion must be 'MoveJ' or 'MoveL', got '{transit_motion}'.",
            )

        # Resolve all symbols up front
        ctx = RobotContext.instance()
        if ctx is None:
            return _CTX_NOT_INITIALIZED
        item_obj, err = _resolve(item, ctx)
        if err:
            return err
        pick_approach_obj, err = _resolve(pick_approach, ctx)
        if err:
            return err
        pick_target_obj, err = _resolve(pick_target, ctx)
        if err:
            return err
        place_approach_obj, err = _resolve(place_approach, ctx)
        if err:
            return err
        place_target_obj, err = _resolve(place_target, ctx)
        if err:
            return err
        home_position_obj, err = _resolve(home_position, ctx)
        if err:
            return err

        # Step 1: move to pick approach point
        logger.info("Step 1/10: %s to home_position '%s'...", initial_motion, home_position)
        result = self.primitives[initial_motion].execute(target=home_position_obj)
        if not result.success:
            return result

        
        logger.info("Step 2/10: %s to pick_approach '%s'...", 'MoveL', pick_approach)
        result = self.primitives['MoveL'].execute(target=pick_approach_obj)
        if not result.success:
            return result

        # Step 2: linear precise approach to pick point
        logger.info("Step 3/10: MoveL to pick_target '%s'...", pick_target)
        result = self.primitives['MoveL'].execute(target=pick_target_obj)
        if not result.success:
            return result

        # Step 3: grasp workpiece
        logger.info("Step 4/10: Grasp '%s'...", item)
        result = self.primitives['Grasp'].execute(expected_item=item_obj)
        if not result.success:
            return result

        # Step 4: linear depart (retrace approach with workpiece)
        logger.info("Step 5/10: MoveL depart to pick_approach '%s'...", pick_approach)
        result = self.primitives['MoveL'].execute(target=pick_approach_obj)
        if not result.success:
            return result

        # Step 5: transit to place approach (with workpiece)
        logger.info("Step 6/10: %s transit to place_approach '%s'...", transit_motion, place_approach)
        result = self.primitives[transit_motion].execute(target=place_approach_obj)
        if not result.success:
            return result

        # Step 6: linear precise approach to place point
        logger.info("Step 7/10: MoveL to place_target '%s'...", place_target)
        result = self.primitives['MoveL'].execute(target=place_target_obj)
        if not result.success:
            return result

        # Step 7: release workpiece
        logger.info("Step 8/10: Release '%s'...", item)
        result = self.primitives['Release'].execute(expected_item=item_obj)
        if not result.success:
            return result

        # Step 8: linear depart (retrace approach, empty gripper)
        logger.info("Step 9/10: MoveL depart to place approach. '%s'...", place_approach_obj)
        result = self.primitives['MoveL'].execute(target=place_approach_obj)
        if not result.success:
            return result
        
        # Step 10: Linear move back to home position
        logger.info("Step 10/10: MoveL to home_position '%s'...", home_position)
        result = self.primitives['MoveL'].execute(target=home_position_obj)
        if not result.success:
            return result
        
        
        logger.info("Pick and place completed: '%s' moved from '%s' to '%s'.", item, pick_target, place_target)
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message=f"Pick and place completed: '{item}' moved from '{pick_target}' to '{place_target}'.",
        )

    # ------------------------------------------------------------------
    # try_execute
    # ------------------------------------------------------------------

    def try_execute(
        self,
        item: str,
        home_position: str,
        pick_approach: str,
        pick_target: str,
        place_approach: str,
        place_target: str,
        transit_motion: str = "MoveL",
        initial_motion: str = "MoveL",
    ) -> SkillResult:
        """Run pre-flight check, then execute pick-and-place if the check passed.

        Args:
            item:            RoboDK name of the workpiece to grasp/release.
            home_position:   Target name for the initial and final home position.
            pick_approach:   Target name for the approach/depart point near pick.
            pick_target:     Target name for the linear-move precise grasp point.
            place_approach:  Target name for the transit destination / depart point near place.
            place_target:    Target name for the linear-move precise place point.
            transit_motion:  Motion type for the pick_approach→place_approach segment.
                             Must be "MoveL" (default) or "MoveJ".
            initial_motion:  Motion type for the initial move to pick_approach.
                             Must be "MoveL" (default) or "MoveJ".

        Returns:
            SkillResult — the check result on pre-flight failure, otherwise the execute result.
        """
        if self._should_skip_check():
            logger.debug("Skipping pre-flight check (debug_skip_check=True)")
            return self.execute(item, home_position, pick_approach, pick_target, place_approach, place_target, transit_motion, initial_motion)
        result = self.check(item, home_position, pick_approach, pick_target, place_approach, place_target, transit_motion, initial_motion)
        if not result.success:
            logger.warning("Pre-flight check failed: %s", result.message)
            return result
        return self.execute(item, home_position, pick_approach, pick_target, place_approach, place_target, transit_motion, initial_motion)
