from __future__ import annotations

import os
from typing import Any

import numpy as np

from SkiLib.base import RobotState
from SkiLib.genesis.config import PLACEMENT_TILT_TOL_DEG, PLACEMENT_XY_TOL_M, PLACEMENT_Z_TOL_M
from SkiLib.genesis.scene import TCP_OFFSET_Z, build_genesis_scene
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
            "active_tool": "Robotiq_2F_85",
            "grasped": [self.held_item_name] if self.held_item_name else [],
        }

    @staticmethod
    def _disc_tilt_deg(entity) -> float | None:
        """Return the tilt angle (degrees) of the gear disc from horizontal.

        The gear disc normal is the local Y axis in STL coords. euler=(-90,0,0)
        at spawn rotates it to world +Z (flat).  After physics this may deviate.

        Rotates local Y = (0,1,0) by the entity's current world quaternion
        (w, x, y, z convention) and returns arccos of the Z component.

        Returns None if the entity does not expose get_quat().
        """
        try:
            raw_q = entity.get_quat()
            q = raw_q.tolist() if hasattr(raw_q, "tolist") else list(raw_q)
            w, x, y, z = float(q[0]), float(q[1]), float(q[2]), float(q[3])
            # R(q) * (0,1,0) — only the Z component needed for tilt check.
            disc_z = 2.0 * (y * z - w * x)
            return float(np.degrees(np.arccos(np.clip(disc_z, -1.0, 1.0))))
        except Exception:
            return None

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

        nearest_name: str | None = None
        nearest_dist = float("inf")
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
        if nearest_name:
            target = self.bundle.targets[nearest_name]
            expected_z = target.pose.pos[2] - TCP_OFFSET_Z
            z_err  = float(abs(pos[2] - expected_z))
            xy_ok  = nearest_dist <= PLACEMENT_XY_TOL_M
            z_ok   = z_err <= PLACEMENT_Z_TOL_M
            tilt_deg = self._disc_tilt_deg(obj.entity)
            # If orientation is unreadable, skip tilt check (fail open).
            tilt_ok = (tilt_deg is None) or (tilt_deg <= PLACEMENT_TILT_TOL_DEG)
            placed  = xy_ok and z_ok and tilt_ok

            tilt_str = f"{tilt_deg:.1f}°" if tilt_deg is not None else "n/a"
            if placed:
                desc = (
                    f"'{name}' PLACED at '{nearest_name}' — "
                    f"XY {nearest_dist*1000:.1f} mm, "
                    f"Z offset {z_err*1000:.1f} mm, tilt {tilt_str}."
                )
            elif not xy_ok:
                desc = (
                    f"'{name}' NOT PLACED — XY {nearest_dist*1000:.1f} mm from '{nearest_name}' "
                    f"(limit {PLACEMENT_XY_TOL_M*1000:.0f} mm). "
                    f"Z offset {z_err*1000:.1f} mm, tilt {tilt_str}."
                )
            elif not z_ok:
                desc = (
                    f"'{name}' NOT PLACED — Z offset {z_err*1000:.1f} mm "
                    f"(limit {PLACEMENT_Z_TOL_M*1000:.0f} mm); gear may have fallen. "
                    f"XY {nearest_dist*1000:.1f} mm, tilt {tilt_str}."
                )
            else:
                desc = (
                    f"'{name}' NOT PLACED — gear tilted {tilt_str} "
                    f"(limit {PLACEMENT_TILT_TOL_DEG:.0f}°). "
                    f"XY {nearest_dist*1000:.1f} mm, Z offset {z_err*1000:.1f} mm."
                )
        else:
            desc = f"'{name}' has no registered place target nearby."

        return {
            "item": name,
            "nearest_place_target": nearest_name,
            "xy_distance_to_nearest_place_m": round(nearest_dist, 4),
            "z_offset_to_expected_m": round(z_err, 4),
            "tilt_angle_deg": round(tilt_deg, 1) if tilt_deg is not None else None,
            "is_placed": placed,
            "description": desc,
        }

    def open_gripper(self) -> None:
        """Command gripper DOFs to the fully-open position (0 rad for Robotiq 2F-85).

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
        self.scene.reset()
        self.open_gripper()
