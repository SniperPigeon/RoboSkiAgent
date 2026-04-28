from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from SkiLib.genesis.types import SceneTarget, TargetPose


@dataclass(frozen=True)
class IKResult:
    success: bool
    qpos: np.ndarray | None
    error: np.ndarray


def as_numpy(value) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    elif hasattr(value, "cpu"):
        value = value.cpu().numpy()
    return np.asarray(value, dtype=float)


def current_qpos(runtime) -> np.ndarray:
    return as_numpy(runtime.robot.get_qpos())


def current_arm_qpos(runtime) -> np.ndarray:
    qpos = current_qpos(runtime)
    return qpos[runtime.bundle.arm_dofs]


def target_to_pose(target: SceneTarget) -> TargetPose:
    return target.pose


def get_tcp_link(runtime):
    return runtime.robot.get_link(runtime.bundle.tcp_link_name)


def get_tcp_pos(runtime) -> np.ndarray:
    link = get_tcp_link(runtime)
    return as_numpy(runtime.robot.get_links_pos([link.idx_local]))[0]


def solve_ik(runtime, target: SceneTarget | TargetPose, init_qpos=None) -> IKResult:
    pose = target.pose if isinstance(target, SceneTarget) else target
    link = get_tcp_link(runtime)
    if init_qpos is None:
        init_qpos = current_qpos(runtime)

    qpos, error = runtime.robot.inverse_kinematics(
        link=link,
        pos=np.asarray(pose.pos, dtype=float),
        quat=np.asarray(pose.quat, dtype=float),
        init_qpos=np.asarray(init_qpos, dtype=float),
        dofs_idx_local=runtime.bundle.arm_dofs,
        return_error=True,
        max_samples=30,
        max_solver_iters=30,
    )
    qpos_np = as_numpy(qpos)
    error_np = as_numpy(error)
    success = bool(np.linalg.norm(error_np[:3]) < 0.005 and np.linalg.norm(error_np[3:]) < 0.05)
    return IKResult(success=success, qpos=qpos_np, error=error_np)


def validate_joint_target(runtime, target: Iterable[float]) -> np.ndarray:
    q = np.asarray(list(target), dtype=float)
    n_arm = len(runtime.bundle.arm_dofs)
    if q.shape[0] == n_arm:
        full = current_qpos(runtime)
        full[runtime.bundle.arm_dofs] = q
        return full
    if q.shape[0] == runtime.robot.n_dofs:
        return q
    raise ValueError(f"Expected {n_arm} arm joints or {runtime.robot.n_dofs} full qpos values, got {q.shape[0]}.")


def control_to_qpos(
    runtime,
    qpos: np.ndarray,
    *,
    max_steps: int = 240,
    tolerance: float = 0.02,
    settle_tolerance: float = 0.08,
) -> tuple[bool, float]:
    arm_dofs = runtime.bundle.arm_dofs
    target_arm = np.asarray(qpos, dtype=float)[arm_dofs]

    for _ in range(max_steps):
        runtime.robot.control_dofs_position(target_arm, dofs_idx_local=arm_dofs)
        runtime.scene.step()
        current = current_arm_qpos(runtime)
        err = float(np.linalg.norm(current - target_arm))
        if err <= tolerance:
            return True, err

    current = current_arm_qpos(runtime)
    err = float(np.linalg.norm(current - target_arm))
    if err <= settle_tolerance:
        runtime.robot.set_dofs_position(target_arm, dofs_idx_local=arm_dofs)
        runtime.scene.step()
        current = current_arm_qpos(runtime)
        return True, float(np.linalg.norm(current - target_arm))
    return False, err


def interpolate_positions(start: np.ndarray, end: np.ndarray, steps: int) -> list[np.ndarray]:
    steps = max(2, int(steps))
    return [start + (end - start) * (i / (steps - 1)) for i in range(steps)]
