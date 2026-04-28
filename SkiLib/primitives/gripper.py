from typing import Optional

from SkiLib.base import (
    BasePrimitive,
    ERROR_INVALID_PARAM,
    ExecutionPhase,
    SkillResult,
    require_robot_active,
)
from SkiLib.genesis.types import SceneObject

ERROR_ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
ERROR_GRIPPER_FAILURE = "GRIPPER_FAILURE"
ERROR_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class Grasp(BasePrimitive):
    """Genesis gripper close + kinematic attachment primitive."""

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not isinstance(expected_item, SceneObject):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Grasp expected_item. Expected a SceneObject.",
            )
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.PLANNING,
            error_type=ERROR_NOT_IMPLEMENTED,
            message="Genesis Grasp proximity check is not implemented yet.",
            suggestion="Implement TCP-to-object distance check and kinematic attachment.",
            data={"expected_item": expected_item.name},
        )

    @require_robot_active
    def execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.EXECUTION,
            error_type=ERROR_NOT_IMPLEMENTED,
            robot_state=self.runtime.get_current_state(),
            message="Genesis Grasp execution is not implemented yet.",
            suggestion="Next migration step: implement gripper close command and held_item state.",
        )

    def try_execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(expected_item, tool)
            if not check.success:
                return check
        return self.execute(expected_item, tool)


class Release(BasePrimitive):
    """Genesis gripper open + release primitive."""

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not isinstance(expected_item, SceneObject):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Release expected_item. Expected a SceneObject.",
            )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Release target '{expected_item.name}' exists in the Genesis scene.",
        )

    @require_robot_active
    def execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.EXECUTION,
            error_type=ERROR_NOT_IMPLEMENTED,
            robot_state=self.runtime.get_current_state(),
            message="Genesis Release execution is not implemented yet.",
            suggestion="Next migration step: implement gripper open command and clear held_item state.",
        )

    def try_execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(expected_item, tool)
            if not check.success:
                return check
        return self.execute(expected_item, tool)
