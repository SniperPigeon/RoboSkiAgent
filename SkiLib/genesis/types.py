from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np


TargetKind = Literal["home", "approach", "pick", "place"]


@dataclass(frozen=True)
class TargetPose:
    name: str
    pos: tuple[float, float, float]
    quat: tuple[float, float, float, float]
    kind: TargetKind
    # TCP yaw used by motion planning. For place targets this may differ from
    # the final object yaw because grasp preserves the object-to-TCP transform.
    yaw_deg: float | None = None
    expected_object_yaw_deg: float | None = None


@dataclass(frozen=True)
class SceneTarget:
    name: str
    pose: TargetPose


@dataclass
class SceneObject:
    name: str
    entity: Any


@dataclass
class GenesisSceneBundle:
    scene: Any
    robot: Any
    objects: dict[str, SceneObject]
    targets: dict[str, SceneTarget]
    tools: dict[str, Any]
    arm_dofs: np.ndarray
    gripper_dofs: np.ndarray
    home_qpos: np.ndarray
    tcp_link_name: str
