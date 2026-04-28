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
