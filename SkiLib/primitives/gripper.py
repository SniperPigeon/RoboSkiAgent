from typing import Optional

import numpy as np

from SkiLib.base import (
    BasePrimitive,
    ERROR_INVALID_PARAM,
    ExecutionPhase,
    SkillResult,
    require_robot_active,
)
from SkiLib.genesis.motion import get_tcp_pos
from SkiLib.genesis.types import SceneObject

ERROR_ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
ERROR_GRIPPER_FAILURE = "GRIPPER_FAILURE"

# Distance threshold for considering the TCP "close enough" to attempt a grasp.
GRASP_PROXIMITY_THRESHOLD = 0.18  # metres


class Grasp(BasePrimitive):
    """Genesis gripper close + weld-constraint attachment primitive."""

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
        if self.runtime.held_item_name is not None:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_GRIPPER_FAILURE,
                message=f"Gripper is already holding '{self.runtime.held_item_name}'. Release before grasping.",
                suggestion="Call Release before attempting a new Grasp.",
            )
        try:
            tcp_pos = get_tcp_pos(self.runtime)
            obj_pos = np.array(expected_item.entity.get_pos().tolist(), dtype=float)
            dist = float(np.linalg.norm(tcp_pos - obj_pos))
            if dist > GRASP_PROXIMITY_THRESHOLD:
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.PLANNING,
                    error_type=ERROR_GRIPPER_FAILURE,
                    message=(
                        f"TCP is {dist:.3f} m from '{expected_item.name}' "
                        f"(threshold {GRASP_PROXIMITY_THRESHOLD} m). Move closer before grasping."
                    ),
                    suggestion="Execute a MoveL to the pick target before calling Grasp.",
                    data={"tcp_to_object_dist": dist},
                )
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_GRIPPER_FAILURE,
                message=f"Grasp proximity check failed: {type(e).__name__}: {e}",
            )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Grasp pre-check passed. TCP is {dist:.3f} m from '{expected_item.name}'.",
            data={"tcp_to_object_dist": dist},
        )

    @require_robot_active
    def execute(self, expected_item: SceneObject) -> SkillResult:
        return self._submit_to_controller(self._execute_body, expected_item)

    def _execute_body(self, expected_item: SceneObject) -> SkillResult:
        if not isinstance(expected_item, SceneObject):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Grasp expected_item. Expected a SceneObject.",
            )
        if self.runtime.held_item_name is not None:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Gripper is already holding '{self.runtime.held_item_name}'.",
                suggestion="Call Release before attempting a new Grasp.",
            )
        try:
            tcp_link = self.runtime.robot.get_link(self.runtime.bundle.tcp_link_name)
            obj_link = expected_item.entity.base_link
            self.runtime.rigid_solver.add_weld_constraint(obj_link.idx, tcp_link.idx)
            self.runtime.held_item_name = expected_item.name
            self.runtime._weld_pair = (obj_link.idx, tcp_link.idx)
            self.runtime.scene.step()
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Grasp weld constraint failed: {type(e).__name__}: {e}",
                suggestion="Check that the object has not already been welded.",
            )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            robot_state=self.runtime.get_current_state(),
            message=f"Grasped '{expected_item.name}' via weld constraint.",
            data={"held_item": expected_item.name},
        )

    def try_execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(expected_item, tool)
            if not check.success:
                return check
        return self.execute(expected_item)


class Release(BasePrimitive):
    """Genesis gripper open + weld-constraint release primitive."""

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
        if self.runtime.held_item_name != expected_item.name:
            held = self.runtime.held_item_name or "nothing"
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_GRIPPER_FAILURE,
                message=f"Cannot release '{expected_item.name}': gripper is holding '{held}'.",
                suggestion="Ensure the correct item was grasped before calling Release.",
            )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Release pre-check passed for '{expected_item.name}'.",
        )

    @require_robot_active
    def execute(self, expected_item: SceneObject) -> SkillResult:
        return self._submit_to_controller(self._execute_body, expected_item)

    def _execute_body(self, expected_item: SceneObject) -> SkillResult:
        if not isinstance(expected_item, SceneObject):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Release expected_item. Expected a SceneObject.",
            )
        if self.runtime._weld_pair is None or self.runtime.held_item_name != expected_item.name:
            held = self.runtime.held_item_name or "nothing"
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Cannot release '{expected_item.name}': gripper is holding '{held}'.",
                suggestion="Ensure the correct item was grasped before calling Release.",
            )
        try:
            obj_idx, tcp_idx = self.runtime._weld_pair
            self.runtime.rigid_solver.delete_weld_constraint(obj_idx, tcp_idx)
            self.runtime.held_item_name = None
            self.runtime._weld_pair = None
            self.runtime.scene.step()
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Release weld constraint removal failed: {type(e).__name__}: {e}",
            )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            robot_state=self.runtime.get_current_state(),
            message=f"Released '{expected_item.name}', weld constraint removed.",
            data={"released_item": expected_item.name},
        )

    def try_execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(expected_item, tool)
            if not check.success:
                return check
        return self.execute(expected_item)
