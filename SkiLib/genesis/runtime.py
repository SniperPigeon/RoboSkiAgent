from __future__ import annotations

import os
from typing import Any

from SkiLib.base import RobotState
from SkiLib.genesis.scene import build_genesis_scene
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
        try:
            qpos: Any = self.robot.get_qpos()
            if hasattr(qpos, "detach"):
                qpos = qpos.detach().cpu().numpy()
            if hasattr(qpos, "tolist"):
                qpos = qpos.tolist()
            joints = list(qpos)
        except Exception:
            joints = None

        return RobotState(
            joints=joints,
            pose=None,
            gripper_state="CLOSED" if self.held_item_name else "OPEN",
        )

    def get_gripper_state(self) -> dict:
        return {
            "active_tool": "Robotiq_2F_85",
            "grasped": [self.held_item_name] if self.held_item_name else [],
        }
