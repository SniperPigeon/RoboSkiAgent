from __future__ import annotations

import os
from typing import Any

import numpy as np

from SkiLib.base import RobotState
from SkiLib.genesis.config import (
    PLACEMENT_TILT_TOL_DEG,
    PLACEMENT_XY_TOL_M,
    PLACEMENT_YAW_TOL_DEG,
    PLACEMENT_Z_TOL_M,
)
from SkiLib.genesis.scene import (
    APPROACH_CLEARANCE,
    FMB_PICK_YAW_DEG,
    FMB_PART_HEIGHT,
    FMB_PART_REF_Z_FROM_BOTTOM,
    GEAR_THICKNESS,
    TCP_OFFSET_Z,
    build_genesis_scene, make_target,
)
from SkiLib.genesis.types import GenesisSceneBundle, SceneObject, SceneTarget


class GenesisRuntime:
    """Owns the Genesis scene and symbolic registries."""

    def __init__(self, show_viewer: bool | None = None):
        if show_viewer is None:
            show_viewer = os.getenv("ROBOSKI_GENESIS_VIEWER", "0") in {"1", "true", "True"}

        self.bundle: GenesisSceneBundle = build_genesis_scene(show_viewer=show_viewer)
        self.scene = self.bundle.scene
        self.robot = self.bundle.robot
        self.held_item_name: str | None = None
        # Cached weld pair (obj_link.idx, tcp_link.idx) for the active grasp
        self._weld_pair: tuple[int, int] | None = None
        # Names of targets dynamically registered during recovery (cleared on reset)
        self._temp_targets: set[str] = set()
        # Objects that have been semantically assembled and should be held at
        # their snapped pose to avoid tight mesh-contact jitter.
        self._assembled_item_poses: dict[
            str, tuple[tuple[float, float, float], tuple[float, float, float, float]]
        ] = {}

    @property
    def rigid_solver(self):
        return self.scene.sim.rigid_solver

    @property
    def robot_name(self) -> str:
        return "UR16e_Robotiq_Genesis"

    @property
    def is_simulation(self) -> bool:
        return True

    def list_targets(self) -> list[str]:
        return sorted(self.bundle.targets)

    def list_objects(self) -> list[str]:
        return sorted(self.bundle.objects)

    def list_tools(self) -> list[str]:
        return sorted(self.bundle.tools)

    def check_item_exists(self, name: str) -> bool:
        return name in self.bundle.targets or name in self.bundle.objects or name in self.bundle.tools

    def resolve_target(self, name: str) -> SceneTarget:
        try:
            return self.bundle.targets[name]
        except KeyError as exc:
            raise KeyError(f"Target '{name}' not found. Available: {self.list_targets()}") from exc

    def resolve_object(self, name: str) -> SceneObject:
        try:
            return self.bundle.objects[name]
        except KeyError as exc:
            raise KeyError(f"Object '{name}' not found. Available: {self.list_objects()}") from exc

    def resolve_item(self, name: str) -> SceneTarget | SceneObject:
        if name in self.bundle.targets:
            return self.bundle.targets[name]
        if name in self.bundle.objects:
            return self.bundle.objects[name]
        raise KeyError(
            f"Symbol '{name}' not found. "
            f"Targets: {self.list_targets()}; objects: {self.list_objects()}"
        )

    def get_current_state(self) -> RobotState:
        joints = None
        pose = None
        try:
            qpos: Any = self.robot.get_qpos()
            if hasattr(qpos, "detach"):
                qpos = qpos.detach().cpu().numpy()
            if hasattr(qpos, "tolist"):
                qpos = qpos.tolist()
            joints = list(qpos)
        except Exception:
            joints = None

        try:
            from SkiLib.genesis.motion import get_tcp_pos  # noqa: PLC0415

            pose = get_tcp_pos(self).tolist()
        except Exception:
            pose = None

        return RobotState(
            joints=joints,
            pose=pose,
            gripper_state="CLOSED" if self.held_item_name else "OPEN",
        )

    def get_gripper_state(self) -> dict:
        return {
            "active_tool": "Robotiq_2F_140",
            "grasped": [self.held_item_name] if self.held_item_name else [],
        }

    def compute_pick_pose(self, name: str) -> dict:
        """Compute a valid TCP pick pose from the object's current physics position.

        Reads entity.get_pos() and tilt, then registers two temporary scene targets:
          Dynamic_Pick_<name>          — precise grasp TCP position
          Dynamic_Pick_<name>_Approach — approach waypoint APPROACH_CLEARANCE above

        These targets can be passed directly to MoveL by the recovery sub-agent.
        They are removed on the next reset() call.

        Returns is_pickable=False (no targets registered) when tilt exceeds
        PLACEMENT_TILT_TOL_DEG — escalate to HITL in that case.
        """
        obj = self.resolve_object(name)
        raw = obj.entity.get_pos()
        pos_list = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        pos = np.array(pos_list, dtype=float)

        tilt_deg   = self._disc_tilt_deg(obj.entity)
        yaw_deg = self._entity_yaw_deg(obj.entity)
        grasp_yaw_deg = FMB_PICK_YAW_DEG.get(name, yaw_deg or 0.0)
        is_pickable = (tilt_deg is None) or (tilt_deg <= PLACEMENT_TILT_TOL_DEG)

        pick_name     = f"Dynamic_Pick_{name}"
        approach_name = f"Dynamic_Pick_{name}_Approach"

        if is_pickable:
            part_height = FMB_PART_HEIGHT.get(name, GEAR_THICKNESS)
            ref_z_from_bottom = FMB_PART_REF_Z_FROM_BOTTOM.get(name, part_height / 2)
            pick_tcp_z = (
                float(pos[2])
                + (part_height / 2 - ref_z_from_bottom)
                + TCP_OFFSET_Z
            )
            approach_tcp_z = pick_tcp_z + APPROACH_CLEARANCE
            self.bundle.targets[pick_name]     = make_target(
                pick_name,
                (float(pos[0]), float(pos[1]), pick_tcp_z),
                "pick",
                yaw_deg=grasp_yaw_deg,
            )
            self.bundle.targets[approach_name] = make_target(
                approach_name,
                (float(pos[0]), float(pos[1]), approach_tcp_z),
                "approach",
                yaw_deg=grasp_yaw_deg,
            )
            self._temp_targets.add(pick_name)
            self._temp_targets.add(approach_name)

        tilt_str = f"{tilt_deg:.1f}°" if tilt_deg is not None else "n/a"
        yaw_str = f"{yaw_deg:.1f}°" if yaw_deg is not None else "n/a"
        if is_pickable:
            desc = (
                f"'{name}' at ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}) m, "
                f"yaw {yaw_str}, grasp yaw {grasp_yaw_deg:.1f}°, tilt {tilt_str}. "
                f"Targets registered: '{pick_name}', '{approach_name}'."
            )
        else:
            desc = (
                f"'{name}' is tilted {tilt_str} — cannot compute safe pick pose. "
                f"Escalate to HITL."
            )

        return {
            "item":                 name,
            "pick_target_name":     pick_name     if is_pickable else None,
            "approach_target_name": approach_name if is_pickable else None,
            "obj_x":       round(float(pos[0]), 4),
            "obj_y":       round(float(pos[1]), 4),
            "obj_z":       round(float(pos[2]), 4),
            "yaw_angle_deg": round(yaw_deg, 1) if yaw_deg is not None else None,
            "tilt_angle_deg": round(tilt_deg, 1) if tilt_deg is not None else None,
            "is_pickable": is_pickable,
            "description": desc,
        }

    @staticmethod
    def _entity_quat(entity) -> tuple[float, float, float, float] | None:
        """Return entity world quaternion in Genesis (w, x, y, z) order."""
        try:
            raw_q = entity.get_quat()
            q = raw_q.tolist() if hasattr(raw_q, "tolist") else list(raw_q)
            return float(q[0]), float(q[1]), float(q[2]), float(q[3])
        except Exception:
            return None

    @classmethod
    def _entity_yaw_deg(cls, entity) -> float | None:
        """Return world yaw angle around Z in degrees, normalized to [-180, 180)."""
        q = cls._entity_quat(entity)
        if q is None:
            return None
        w, x, y, z = q
        yaw_rad = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
        return float((np.degrees(yaw_rad) + 180.0) % 360.0 - 180.0)

    @staticmethod
    def _angle_diff_deg(a: float, b: float) -> float:
        """Return smallest absolute angular difference in degrees."""
        return float(abs((a - b + 180.0) % 360.0 - 180.0))

    @staticmethod
    def _yaw_quat_wxyz(yaw_deg: float) -> tuple[float, float, float, float]:
        half = np.radians(yaw_deg) / 2.0
        return (float(np.cos(half)), 0.0, 0.0, float(np.sin(half)))

    def snap_object_to_place_target(
        self,
        name: str,
        *,
        max_xy_dist: float = 0.010,
        max_z_err: float = 0.020,
        max_yaw_err_deg: float = 8.0,
        max_tilt_deg: float = 12.0,
    ) -> dict:
        """Snap a nearly placed FMB object to its semantic assembly pose.

        Genesis mesh contacts are too coarse for tight insertion. This helper is
        intentionally conservative: it only snaps when the object is already
        close to its own place target in XY/yaw, preserving genuine placement
        failures for recovery.
        """
        target_name = f"{name}_Place"
        target = self.bundle.targets.get(target_name)
        if target is None:
            return {"snapped": False, "reason": "no_place_target"}

        obj = self.resolve_object(name)
        raw = obj.entity.get_pos()
        pos_list = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        pos = np.array(pos_list, dtype=float)

        target_xy = np.array(target.pose.pos[:2], dtype=float)
        xy_dist = float(np.linalg.norm(pos[:2] - target_xy))

        part_height = FMB_PART_HEIGHT.get(name, GEAR_THICKNESS)
        ref_z_from_bottom = FMB_PART_REF_Z_FROM_BOTTOM.get(name, part_height / 2)
        bottom_z = target.pose.pos[2] - TCP_OFFSET_Z - part_height / 2
        expected_z = bottom_z + ref_z_from_bottom
        z_err = float(abs(pos[2] - expected_z))

        yaw_deg = self._entity_yaw_deg(obj.entity)
        target_yaw_deg = target.pose.expected_object_yaw_deg
        yaw_err = (
            self._angle_diff_deg(yaw_deg, target_yaw_deg)
            if yaw_deg is not None and target_yaw_deg is not None
            else 0.0
        )
        tilt_deg = self._disc_tilt_deg(obj.entity)
        tilt_err = float(tilt_deg or 0.0)

        if (
            xy_dist > max_xy_dist
            or z_err > max_z_err
            or yaw_err > max_yaw_err_deg
            or tilt_err > max_tilt_deg
        ):
            return {
                "snapped": False,
                "reason": "outside_snap_window",
                "xy_distance_m": round(xy_dist, 4),
                "z_error_m": round(z_err, 4),
                "yaw_error_deg": round(yaw_err, 1),
                "tilt_deg": round(tilt_err, 1),
                "limits": {
                    "max_xy_dist_m": max_xy_dist,
                    "max_z_err_m": max_z_err,
                    "max_yaw_err_deg": max_yaw_err_deg,
                    "max_tilt_deg": max_tilt_deg,
                },
            }

        snapped_pos = (float(target_xy[0]), float(target_xy[1]), bottom_z + ref_z_from_bottom)
        snapped_yaw = float(target_yaw_deg or 0.0)

        obj.entity.set_pos(snapped_pos)
        snapped_quat = self._yaw_quat_wxyz(snapped_yaw)
        obj.entity.set_quat(snapped_quat)
        self._assembled_item_poses[name] = (snapped_pos, snapped_quat)
        return {
            "snapped": True,
            "target": target_name,
            "pos": [round(v, 4) for v in snapped_pos],
            "yaw_deg": round(snapped_yaw, 1),
            "xy_distance_m": round(xy_dist, 4),
            "z_error_m": round(z_err, 4),
            "yaw_error_deg": round(yaw_err, 1),
            "tilt_deg": round(tilt_err, 1),
        }

    def unmark_assembled_object(self, name: str) -> None:
        """Allow an assembled object to move again, e.g. before re-grasping."""
        self._assembled_item_poses.pop(name, None)

    def stabilize_assembled_objects(self) -> None:
        """Keep semantically placed objects fixed at their snapped assembly pose."""
        for name, (pos, quat) in list(self._assembled_item_poses.items()):
            obj = self.bundle.objects.get(name)
            if obj is None:
                self._assembled_item_poses.pop(name, None)
                continue
            obj.entity.set_pos(pos)
            obj.entity.set_quat(quat)

    @classmethod
    def _disc_tilt_deg(cls, entity) -> float | None:
        """Return the tilt angle (degrees) from the mesh's local Z axis to world Z.

        FMB meshes are Z-up. Rotates local Z = (0,0,1) by the entity's current
        world quaternion (w, x, y, z convention) and returns arccos of the
        resulting world-Z component.

        Returns None if the entity does not expose get_quat().
        """
        q = cls._entity_quat(entity)
        if q is None:
            return None
        w, x, y, z = q
        # R(q) * (0,0,1) — only the Z component needed for tilt check.
        local_z_world_z = 1.0 - 2.0 * (x * x + y * y)
        return float(np.degrees(np.arccos(np.clip(local_z_world_z, -1.0, 1.0))))

    def get_object_position(self, name: str) -> dict:
        """Return placement status of a scene object relative to registered place targets.

        Checks XY proximity, Z height, AND disc tilt to detect misplaced gears:
        - XY tolerance:   PLACEMENT_XY_TOL_M  (distinguishes 40 mm-spaced shaft slots)
        - Z tolerance:    PLACEMENT_Z_TOL_M   (expected_z = place_tcp_z - TCP_OFFSET_Z)
        - Tilt tolerance: PLACEMENT_TILT_TOL_DEG  (disc normal vs world +Z)

        Thread-safe read — no physics mutation.
        """
        obj = self.resolve_object(name)
        raw = obj.entity.get_pos()
        pos_list = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        pos = np.array(pos_list, dtype=float)

        direct_place_name = f"{name}_Place"
        nearest_name: str | None = (
            direct_place_name if direct_place_name in self.bundle.targets else None
        )
        nearest_dist = float("inf")
        if nearest_name is not None:
            target = self.bundle.targets[nearest_name]
            txy = np.array(target.pose.pos[:2], dtype=float)
            nearest_dist = float(np.linalg.norm(pos[:2] - txy))
        else:
            for tname, target in self.bundle.targets.items():
                if target.pose.kind != "place":
                    continue
                txy = np.array(target.pose.pos[:2], dtype=float)
                dist = float(np.linalg.norm(pos[:2] - txy))
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_name = tname

        placed = False
        z_err = float("inf")
        tilt_deg: float | None = None
        yaw_deg = self._entity_yaw_deg(obj.entity)
        if nearest_name:
            target = self.bundle.targets[nearest_name]
            target_item = nearest_name.removesuffix("_Place")
            target_height = FMB_PART_HEIGHT.get(target_item, GEAR_THICKNESS)
            target_ref_z_from_bottom = FMB_PART_REF_Z_FROM_BOTTOM.get(
                target_item, target_height / 2
            )
            expected_bottom_z = target.pose.pos[2] - TCP_OFFSET_Z - target_height / 2
            expected_z = expected_bottom_z + target_ref_z_from_bottom
            z_err  = float(abs(pos[2] - expected_z))
            xy_ok  = nearest_dist <= PLACEMENT_XY_TOL_M
            z_ok   = z_err <= PLACEMENT_Z_TOL_M
            tilt_deg = self._disc_tilt_deg(obj.entity)
            # If orientation is unreadable, skip tilt check (fail open).
            tilt_ok = (tilt_deg is None) or (tilt_deg <= PLACEMENT_TILT_TOL_DEG)
            target_yaw_deg = target.pose.expected_object_yaw_deg
            yaw_err = (
                self._angle_diff_deg(yaw_deg, target_yaw_deg)
                if yaw_deg is not None and target_yaw_deg is not None
                else None
            )
            yaw_ok = (yaw_err is None) or (yaw_err <= PLACEMENT_YAW_TOL_DEG)
            placed  = xy_ok and z_ok and tilt_ok and yaw_ok

            tilt_str = f"{tilt_deg:.1f}°" if tilt_deg is not None else "n/a"
            yaw_str = f"{yaw_deg:.1f}°" if yaw_deg is not None else "n/a"
            target_yaw_str = f"{target_yaw_deg:.1f}°" if target_yaw_deg is not None else "n/a"
            yaw_err_str = f"{yaw_err:.1f}°" if yaw_err is not None else "n/a"
            if placed:
                desc = (
                    f"'{name}' PLACED at '{nearest_name}' — "
                    f"XY {nearest_dist*1000:.1f} mm, "
                    f"Z offset {z_err*1000:.1f} mm, object yaw {yaw_str} "
                    f"(target {target_yaw_str}, err {yaw_err_str}), tilt {tilt_str}."
                )
            elif not xy_ok:
                desc = (
                    f"'{name}' NOT PLACED — XY {nearest_dist*1000:.1f} mm from '{nearest_name}' "
                    f"(limit {PLACEMENT_XY_TOL_M*1000:.0f} mm). "
                    f"Z offset {z_err*1000:.1f} mm, object yaw {yaw_str} "
                    f"(target {target_yaw_str}, err {yaw_err_str}), tilt {tilt_str}."
                )
            elif not z_ok:
                desc = (
                    f"'{name}' NOT PLACED — Z offset {z_err*1000:.1f} mm "
                    f"(limit {PLACEMENT_Z_TOL_M*1000:.0f} mm); gear may have fallen. "
                    f"XY {nearest_dist*1000:.1f} mm, object yaw {yaw_str} "
                    f"(target {target_yaw_str}, err {yaw_err_str}), tilt {tilt_str}."
                )
            elif not tilt_ok:
                desc = (
                    f"'{name}' NOT PLACED — gear tilted {tilt_str} "
                    f"(limit {PLACEMENT_TILT_TOL_DEG:.0f}°). "
                    f"XY {nearest_dist*1000:.1f} mm, Z offset {z_err*1000:.1f} mm, "
                    f"object yaw {yaw_str}."
                )
            else:
                desc = (
                    f"'{name}' NOT PLACED — object yaw {yaw_str} is {yaw_err_str} from "
                    f"target object yaw {target_yaw_str} (limit {PLACEMENT_YAW_TOL_DEG:.0f}°). "
                    f"XY {nearest_dist*1000:.1f} mm, Z offset {z_err*1000:.1f} mm, "
                    f"tilt {tilt_str}."
                )
        else:
            desc = f"'{name}' has no registered place target nearby."

        return {
            "item": name,
            "nearest_place_target": nearest_name,
            "xy_distance_to_nearest_place_m": round(nearest_dist, 4),
            "z_offset_to_expected_m": round(z_err, 4),
            "yaw_angle_deg": round(yaw_deg, 1) if yaw_deg is not None else None,
            "target_yaw_angle_deg": round(target_yaw_deg, 1) if nearest_name and target_yaw_deg is not None else None,
            "target_object_yaw_angle_deg": round(target_yaw_deg, 1) if nearest_name and target_yaw_deg is not None else None,
            "yaw_error_deg": round(yaw_err, 1) if nearest_name and yaw_err is not None else None,
            "tilt_angle_deg": round(tilt_deg, 1) if tilt_deg is not None else None,
            "is_placed": placed,
            "description": desc,
        }

    def open_gripper(self) -> None:
        """Command gripper DOFs to the fully-open position (0 rad for Robotiq 2F-140).

        Uses set_dofs_position for an instant position override so the open state
        is reflected immediately in the physics snapshot and in the viewer.
        """
        gripper_dofs = self.bundle.gripper_dofs
        if len(gripper_dofs) == 0:
            return
        open_qpos = np.zeros(len(gripper_dofs), dtype=float)
        self.robot.set_dofs_position(open_qpos, dofs_idx_local=gripper_dofs)
        self.robot.control_dofs_position(open_qpos, dofs_idx_local=gripper_dofs)

    def reset(self) -> None:
        """Reset physics to home state and clear gripper constraints.

        Must be called from the Genesis thread (via GenesisController.submit).
        scene.reset() restores all rigid-body positions/velocities to the
        snapshot taken at the end of build_genesis_scene() — robot at home_qpos,
        all parts at their tray/assembly positions, no weld constraints.
        """
        if self._weld_pair is not None:
            try:
                self.rigid_solver.delete_weld_constraint(*self._weld_pair)
            except Exception:
                pass
            self._weld_pair = None
        self.held_item_name = None
        self._assembled_item_poses.clear()
        for tname in self._temp_targets:
            self.bundle.targets.pop(tname, None)
        self._temp_targets.clear()
        self.scene.reset()
        self.open_gripper()
