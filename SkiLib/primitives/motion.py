from typing import List, Union

from SkiLib.base import (
    BasePrimitive,
    ERROR_INVALID_PARAM,
    ExecutionPhase,
    RobotState,
    SkillResult,
    require_robot_active,
)
from SkiLib.genesis.types import SceneTarget

ERROR_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def _snapshot(runtime) -> RobotState:
    return runtime.get_current_state()


class MoveJ(BasePrimitive):
    """Genesis joint-space point-to-point motion primitive."""

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, target: Union[SceneTarget, List[float]], ref_frame=None) -> SkillResult:
        if isinstance(target, SceneTarget):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_NOT_IMPLEMENTED,
                message="Genesis MoveJ IK check is not implemented yet.",
                suggestion="Implement Genesis IK in SkiLib/genesis/motion.py before executing motion tasks.",
                data={"target": target.name},
            )
        if isinstance(target, list):
            if len(target) < len(self.runtime.bundle.arm_dofs):
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_INVALID_PARAM,
                    message="Joint target does not contain enough arm DOF values.",
                )
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_NOT_IMPLEMENTED,
                message="Genesis MoveJ joint-list execution is not implemented yet.",
            )
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_INVALID_PARAM,
            message="Invalid MoveJ target. Expected a SceneTarget or joint list.",
        )

    @require_robot_active
    def execute(self, target: Union[SceneTarget, List[float]], blocking: bool = True, ref_frame=None) -> SkillResult:
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.EXECUTION,
            error_type=ERROR_NOT_IMPLEMENTED,
            robot_state=_snapshot(self.runtime),
            message="Genesis MoveJ execution is not implemented yet.",
            suggestion="Next migration step: implement IK, PD control, and convergence checks.",
        )

    def try_execute(self, target: Union[SceneTarget, List[float]], ref_frame=None, blocking: bool = True) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(target, ref_frame)
            if not check.success:
                return check
        return self.execute(target, blocking, ref_frame)


class MoveL(BasePrimitive):
    """Genesis Cartesian linear motion primitive."""

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, target: SceneTarget, ref_frame=None) -> SkillResult:
        if not isinstance(target, SceneTarget):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid MoveL target. Expected a SceneTarget.",
            )
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.PLANNING,
            error_type=ERROR_NOT_IMPLEMENTED,
            message="Genesis MoveL waypoint IK check is not implemented yet.",
            suggestion="Implement Cartesian waypoint sampling and IK before executing linear motion tasks.",
            data={"target": target.name},
        )

    @require_robot_active
    def execute(self, target: SceneTarget, ref_frame=None, blocking: bool = True) -> SkillResult:
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.EXECUTION,
            error_type=ERROR_NOT_IMPLEMENTED,
            robot_state=_snapshot(self.runtime),
            message="Genesis MoveL execution is not implemented yet.",
            suggestion="Next migration step: implement TCP pose interpolation and waypoint tracking.",
        )

    def try_execute(self, target: SceneTarget, ref_frame=None, blocking: bool = True) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(target, ref_frame)
            if not check.success:
                return check
        return self.execute(target, ref_frame, blocking)
