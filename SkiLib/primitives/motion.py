from typing import List, Union

import numpy as np

from SkiLib.base import (
    BasePrimitive,
    ERROR_IK_FAILURE,
    ERROR_INVALID_PARAM,
    ERROR_TIMEOUT,
    ExecutionPhase,
    RobotState,
    SkillResult,
    require_robot_active,
)
from SkiLib.genesis.motion import (
    IKResult,
    control_to_qpos,
    current_qpos,
    get_tcp_pos,
    interpolate_positions,
    solve_ik,
    validate_joint_target,
)
from SkiLib.genesis.types import SceneTarget
from SkiLib.log import get_logger

logger = get_logger(__name__)


def _target_orientation_data(target: SceneTarget) -> dict:
    w, x, y, z = (float(v) for v in target.pose.quat)
    yaw = float((np.degrees(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))) + 180.0) % 360.0 - 180.0)
    return {
        "target_quat_wxyz": [w, x, y, z],
        "target_yaw_deg": round(yaw, 1),
        "target_tcp_yaw_deg": round(yaw, 1),
        "target_object_yaw_deg": (
            round(target.pose.expected_object_yaw_deg, 1)
            if target.pose.expected_object_yaw_deg is not None
            else None
        ),
    }


def _snapshot(runtime) -> RobotState:
    return runtime.get_current_state()


def _ik_failure_result(target_name: str, ik: IKResult) -> SkillResult:
    logger.warning(
        "IK failure: target=%s error=%s",
        target_name,
        ik.error.tolist() if hasattr(ik.error, "tolist") else ik.error,
    )
    return SkillResult(
        success=False,
        execution_phase=ExecutionPhase.PLANNING,
        error_type=ERROR_IK_FAILURE,
        message=f"Genesis IK failed for target '{target_name}'.",
        suggestion="Check whether the target is inside the reachable workspace and whether the TCP orientation is feasible.",
        data={"ik_error": ik.error.tolist()},
    )


class MoveJ(BasePrimitive):
    """Genesis joint-space point-to-point motion primitive."""

    TOOL_NAME = "MoveJ"
    TOOL_DESCRIPTION = "Move the robot to a named Genesis target using joint motion."
    TOOL_PARAMETERS = {
        "target": {
            "type": "str",
            "required": True,
            "description": "Genesis target name to move to with joint motion.",
            "resolver": "target",
        },
    }

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, target: Union[SceneTarget, List[float]], ref_frame=None) -> SkillResult:
        logger.info("MoveJ.check start: target=%s", getattr(target, "name", "joint_target"))
        if isinstance(target, SceneTarget):
            ik = solve_ik(self.runtime, target)
            if not ik.success:
                return _ik_failure_result(target.name, ik)
            logger.info(
                "MoveJ.check passed: target=%s pos=%s yaw=%s",
                target.name,
                [round(float(v), 4) for v in target.pose.pos],
                target.pose.tcp_yaw_deg,
            )
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.PLANNING,
                message=f"MoveJ target '{target.name}' has a valid Genesis IK solution.",
                data={
                    "target": target.name,
                    "qpos": ik.qpos.tolist() if ik.qpos is not None else None,
                    **_target_orientation_data(target),
                },
            )
        if isinstance(target, list):
            try:
                validate_joint_target(self.runtime, target)
            except ValueError as e:
                logger.warning("MoveJ.check failed: invalid joint target %s", e)
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_INVALID_PARAM,
                    message=str(e),
                )
            logger.info("MoveJ.check passed: joint target length=%d", len(target))
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.PLANNING,
                message="MoveJ joint target is valid.",
            )
        logger.warning("MoveJ.check failed: invalid target=%r", target)
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_INVALID_PARAM,
            message="Invalid MoveJ target. Expected a SceneTarget or joint list.",
        )

    @require_robot_active
    def execute(self, target: Union[SceneTarget, List[float]], blocking: bool = True, ref_frame=None) -> SkillResult:
        return self._submit_to_controller(self._execute_body, target, blocking, ref_frame)

    def _execute_body(self, target: Union[SceneTarget, List[float]], blocking: bool = True, ref_frame=None) -> SkillResult:
        try:
            if isinstance(target, SceneTarget):
                start_tcp = get_tcp_pos(self.runtime)
                logger.info(
                    "MoveJ.execute start: target=%s start_tcp=%s target_pos=%s yaw=%s",
                    target.name,
                    np.round(start_tcp, 4).tolist(),
                    [round(float(v), 4) for v in target.pose.pos],
                    target.pose.tcp_yaw_deg,
                )
                ik = solve_ik(self.runtime, target)
                if not ik.success or ik.qpos is None:
                    return _ik_failure_result(target.name, ik)
                qpos = ik.qpos
                target_name = target.name
            elif isinstance(target, list):
                logger.info("MoveJ.execute start: joint_target length=%d", len(target))
                qpos = validate_joint_target(self.runtime, target)
                target_name = "joint_target"
            else:
                logger.warning("MoveJ.execute failed: invalid target=%r", target)
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_INVALID_PARAM,
                    message="Invalid MoveJ target. Expected a SceneTarget or joint list.",
                )

            reached, final_error = control_to_qpos(self.runtime, qpos)
            if not reached:
                logger.warning(
                    "MoveJ.execute timeout: target=%s final_joint_error=%.6f",
                    target_name,
                    final_error,
                )
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.EXECUTION,
                    error_type=ERROR_TIMEOUT,
                    robot_state=_snapshot(self.runtime),
                    message=f"MoveJ to '{target_name}' did not converge before timeout.",
                    suggestion="Increase max_steps/tolerance or verify the target qpos is dynamically reachable.",
                    data={"final_joint_error": final_error},
                )

            state = _snapshot(self.runtime)
            logger.info(
                "MoveJ.execute success: target=%s final_joint_error=%.6f final_tcp=%s",
                target_name,
                final_error,
                np.round(np.array(state.pose, dtype=float), 4).tolist() if state.pose is not None else None,
            )
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                robot_state=state,
                message=f"MoveJ to '{target_name}' executed successfully.",
                data={
                    "joints": state.joints,
                    "final_joint_error": final_error,
                    **(_target_orientation_data(target) if isinstance(target, SceneTarget) else {}),
                },
            )
        except Exception as e:
            logger.warning("MoveJ.execute exception: %s", e, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_TIMEOUT,
                robot_state=_snapshot(self.runtime),
                message=f"MoveJ failed during Genesis execution: {type(e).__name__}",
                suggestion="Check Genesis runtime state and target data.",
            )

    def try_execute(self, target: Union[SceneTarget, List[float]], ref_frame=None, blocking: bool = True) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(target, ref_frame)
            if not check.success:
                return check
        return self.execute(target, blocking, ref_frame)


class MoveL(BasePrimitive):
    """Genesis Cartesian linear motion primitive."""

    TOOL_NAME = "MoveL"
    TOOL_DESCRIPTION = "Move the TCP to a named Genesis target using linear Cartesian motion."
    TOOL_PARAMETERS = {
        "target": {
            "type": "str",
            "required": True,
            "description": "Genesis target name to move to with Cartesian linear motion.",
            "resolver": "target",
        },
    }

    def __init__(self, runtime):
        super().__init__(runtime)

    def _waypoint_qpos(self, target: SceneTarget, steps: int = 20) -> tuple[list, IKResult | None]:
        start = get_tcp_pos(self.runtime)
        end = target.pose.pos
        logger.info(
            "MoveL.waypoints start: target=%s start_tcp=%s end_tcp=%s steps=%d yaw=%s",
            target.name,
            np.round(start, 4).tolist(),
            [round(float(v), 4) for v in end],
            steps,
            target.pose.tcp_yaw_deg,
        )
        qpos_seed = current_qpos(self.runtime)
        qposes = []

        for pos in interpolate_positions(start, end, steps)[1:]:
            waypoint = type(target.pose)(
                name=f"{target.name}_waypoint",
                pos=tuple(float(v) for v in pos),
                quat=target.pose.quat,
                kind=target.pose.kind,
                yaw_deg=target.pose.yaw_deg,
                tcp_yaw_deg=target.pose.tcp_yaw_deg,
                expected_object_yaw_deg=target.pose.expected_object_yaw_deg,
            )
            ik = solve_ik(self.runtime, waypoint, init_qpos=qpos_seed)
            if not ik.success or ik.qpos is None:
                logger.warning(
                    "MoveL.waypoints IK failed: target=%s waypoint_index=%d waypoint_pos=%s",
                    target.name,
                    len(qposes) + 1,
                    [round(float(v), 4) for v in pos],
                )
                return qposes, ik
            qposes.append(ik.qpos)
            qpos_seed = ik.qpos
        logger.info("MoveL.waypoints ready: target=%s count=%d", target.name, len(qposes))
        return qposes, None

    def check(self, target: SceneTarget, ref_frame=None) -> SkillResult:
        logger.info("MoveL.check start: target=%s", getattr(target, "name", target))
        if not isinstance(target, SceneTarget):
            logger.warning("MoveL.check failed: invalid target=%r", target)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid MoveL target. Expected a SceneTarget.",
            )
        qposes, failed = self._waypoint_qpos(target)
        if failed is not None:
            return _ik_failure_result(target.name, failed)
        logger.info(
            "MoveL.check passed: target=%s pos=%s waypoints=%d",
            target.name,
            [round(float(v), 4) for v in target.pose.pos],
            len(qposes),
        )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"MoveL target '{target.name}' has valid waypoint IK solutions.",
            data={"target": target.name, "waypoints": len(qposes), **_target_orientation_data(target)},
        )

    @require_robot_active
    def execute(self, target: SceneTarget, ref_frame=None, blocking: bool = True) -> SkillResult:
        return self._submit_to_controller(self._execute_body, target, ref_frame, blocking)

    def _execute_body(self, target: SceneTarget, ref_frame=None, blocking: bool = True) -> SkillResult:
        logger.info("MoveL.execute start: target=%s", getattr(target, "name", target))
        if not isinstance(target, SceneTarget):
            logger.warning("MoveL.execute failed: invalid target=%r", target)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid MoveL target. Expected a SceneTarget.",
            )
        try:
            qposes, failed = self._waypoint_qpos(target)
            if failed is not None:
                return _ik_failure_result(target.name, failed)

            max_error = 0.0
            for idx, qpos in enumerate(qposes):
                is_final_waypoint = idx == len(qposes) - 1
                reached, final_error = control_to_qpos(
                    self.runtime,
                    qpos,
                    max_steps=260 if is_final_waypoint else 180,
                    tolerance=0.012 if is_final_waypoint else 0.03,
                    settle_tolerance=0.04 if is_final_waypoint else 0.08,
                )
                max_error = max(max_error, final_error)
                if not reached:
                    logger.warning(
                        "MoveL.execute timeout: target=%s waypoint=%d/%d final_joint_error=%.6f",
                        target.name,
                        idx + 1,
                        len(qposes),
                        final_error,
                    )
                    return SkillResult(
                        success=False,
                        execution_phase=ExecutionPhase.EXECUTION,
                        error_type=ERROR_TIMEOUT,
                        robot_state=_snapshot(self.runtime),
                        message=f"MoveL to '{target.name}' did not converge at a waypoint.",
                        suggestion="Increase waypoint tracking steps or reduce Cartesian step size.",
                        data={"final_joint_error": final_error},
                    )

            state = _snapshot(self.runtime)
            logger.info(
                "MoveL.execute success: target=%s waypoints=%d max_joint_error=%.6f final_tcp=%s",
                target.name,
                len(qposes),
                max_error,
                np.round(np.array(state.pose, dtype=float), 4).tolist() if state.pose is not None else None,
            )
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                robot_state=state,
                message=f"MoveL to '{target.name}' executed successfully.",
                data={
                    "joints": state.joints,
                    "max_joint_error": max_error,
                    "waypoints": len(qposes),
                    **_target_orientation_data(target),
                },
            )
        except Exception as e:
            logger.warning("MoveL.execute exception: target=%s error=%s", target.name, e, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_TIMEOUT,
                robot_state=_snapshot(self.runtime),
                message=f"MoveL failed during Genesis execution: {type(e).__name__}",
                suggestion="Check Genesis runtime state and target data.",
            )

    def try_execute(self, target: SceneTarget, ref_frame=None, blocking: bool = True) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(target, ref_frame)
            if not check.success:
                return check
        return self.execute(target, ref_frame, blocking)
