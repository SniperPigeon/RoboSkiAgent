import os
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
from SkiLib.genesis.scene import (
    FMB_PART_HEIGHT,
    FMB_PART_REF_Z_FROM_BOTTOM,
    TCP_OFFSET_Z,
    fmb_grasp_z_from_bottom,
)
from SkiLib.genesis.types import SceneObject
from SkiLib.log import get_logger

logger = get_logger(__name__)

ERROR_ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
ERROR_GRIPPER_FAILURE = "GRIPPER_FAILURE"

# Distance threshold for considering the TCP "close enough" to attempt a grasp.
GRASP_PROXIMITY_THRESHOLD = 0.18  # metres
_GRASP_FAILURE_INJECTED = False


def _consume_grasp_failure_injection() -> bool:
    """Return True when debug config asks the next/all Grasp calls to fail."""
    global _GRASP_FAILURE_INJECTED
    mode = os.getenv("ROBOSKI_INJECT_GRASP_FAILURE", "off").strip().lower()
    if mode in {"1", "true", "yes", "on", "once"}:
        if _GRASP_FAILURE_INJECTED:
            return False
        _GRASP_FAILURE_INJECTED = True
        return True
    if mode == "always":
        return True
    return False


def _object_pose_summary(obj: SceneObject) -> dict:
    """Return a compact object pose snapshot for debug logs."""
    summary = {"pos": None, "quat": None}
    try:
        raw_pos = obj.entity.get_pos()
        pos = raw_pos.tolist() if hasattr(raw_pos, "tolist") else list(raw_pos)
        summary["pos"] = [round(float(v), 4) for v in pos]
    except Exception:
        pass
    try:
        raw_quat = obj.entity.get_quat()
        quat = raw_quat.tolist() if hasattr(raw_quat, "tolist") else list(raw_quat)
        summary["quat"] = [round(float(v), 4) for v in quat]
    except Exception:
        pass
    return summary


class Grasp(BasePrimitive):
    """Genesis gripper close + weld-constraint attachment primitive."""

    TOOL_NAME = "Grasp"
    TOOL_DESCRIPTION = "Close the gripper around a named Genesis workpiece."
    TOOL_PARAMETERS = {
        "expected_item": {
            "type": "str",
            "required": True,
            "description": "Genesis object name of the workpiece to grasp.",
            "resolver": "object",
        },
    }

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        logger.info(
            "Grasp.check start: expected_item=%s held_item=%s",
            getattr(expected_item, "name", expected_item),
            self.runtime.held_item_name,
        )
        if not isinstance(expected_item, SceneObject):
            logger.warning("Grasp.check failed: invalid expected_item=%r", expected_item)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Grasp expected_item. Expected a SceneObject.",
            )
        if self.runtime.held_item_name is not None:
            logger.warning(
                "Grasp.check failed: already holding %s, requested=%s",
                self.runtime.held_item_name,
                expected_item.name,
            )
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
            part_height = FMB_PART_HEIGHT.get(expected_item.name)
            ref_z_from_bottom = FMB_PART_REF_Z_FROM_BOTTOM.get(expected_item.name)
            if part_height is not None and ref_z_from_bottom is not None:
                expected_tcp_pos = obj_pos.copy()
                expected_tcp_pos[2] += (
                    fmb_grasp_z_from_bottom(expected_item.name, part_height)
                    - ref_z_from_bottom
                    + TCP_OFFSET_Z
                )
            else:
                expected_tcp_pos = obj_pos
            dist = float(np.linalg.norm(tcp_pos - expected_tcp_pos))
            logger.info(
                "Grasp.check geometry: item=%s tcp_pos=%s expected_tcp_pos=%s dist=%.4f threshold=%.4f",
                expected_item.name,
                np.round(tcp_pos, 4).tolist(),
                np.round(expected_tcp_pos, 4).tolist(),
                dist,
                GRASP_PROXIMITY_THRESHOLD,
            )
            if dist > GRASP_PROXIMITY_THRESHOLD:
                logger.warning(
                    "Grasp.check failed: item=%s tcp_to_grasp_dist=%.4f threshold=%.4f",
                    expected_item.name,
                    dist,
                    GRASP_PROXIMITY_THRESHOLD,
                )
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.PLANNING,
                    error_type=ERROR_GRIPPER_FAILURE,
                    message=(
                        f"TCP is {dist:.3f} m from '{expected_item.name}' grasp point "
                        f"(threshold {GRASP_PROXIMITY_THRESHOLD} m). Move closer before grasping."
                    ),
                    suggestion="Execute a MoveL to the pick target before calling Grasp.",
                    data={"tcp_to_object_dist": dist},
                )
        except Exception as e:
            logger.warning("Grasp.check failed with exception for %s: %s", expected_item.name, e, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_GRIPPER_FAILURE,
                message=f"Grasp proximity check failed: {type(e).__name__}: {e}",
        )
        logger.info("Grasp.check passed: item=%s dist=%.4f", expected_item.name, dist)
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Grasp pre-check passed. TCP is {dist:.3f} m from '{expected_item.name}' grasp point.",
            data={"tcp_to_object_dist": dist},
        )

    @require_robot_active
    def execute(self, expected_item: SceneObject) -> SkillResult:
        return self._submit_to_controller(self._execute_body, expected_item)

    def _execute_body(self, expected_item: SceneObject) -> SkillResult:
        logger.info(
            "Grasp.execute start: expected_item=%s held_item=%s weld_pair=%s object_pose=%s",
            getattr(expected_item, "name", expected_item),
            self.runtime.held_item_name,
            self.runtime._weld_pair,
            _object_pose_summary(expected_item) if isinstance(expected_item, SceneObject) else None,
        )
        if not isinstance(expected_item, SceneObject):
            logger.warning("Grasp.execute failed: invalid expected_item=%r", expected_item)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Grasp expected_item. Expected a SceneObject.",
            )
        if self.runtime.held_item_name is not None:
            logger.warning(
                "Grasp.execute failed: already holding %s, requested=%s",
                self.runtime.held_item_name,
                expected_item.name,
            )
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Gripper is already holding '{self.runtime.held_item_name}'.",
                suggestion="Call Release before attempting a new Grasp.",
            )
        if _consume_grasp_failure_injection():
            logger.warning(
                "Grasp.execute injected failure: item=%s held_item remains %s weld_pair=%s object_pose=%s",
                expected_item.name,
                self.runtime.held_item_name,
                self.runtime._weld_pair,
                _object_pose_summary(expected_item),
            )
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=(
                    f"Injected grasp failure for '{expected_item.name}': "
                    "gripper attempted to close, but no object was attached."
                ),
                suggestion="Re-align TCP at the pick target and retry Grasp.",
                data={"held_item": None, "injected_fault": True},
            )
        try:
            self.runtime.unmark_assembled_object(expected_item.name)
            tcp_link = self.runtime.robot.get_link(self.runtime.bundle.tcp_link_name)
            obj_link = expected_item.entity.base_link
            self.runtime.rigid_solver.add_weld_constraint(obj_link.idx, tcp_link.idx)
            self.runtime.held_item_name = expected_item.name
            self.runtime._weld_pair = (obj_link.idx, tcp_link.idx)
            self.runtime.scene.step()
            self.runtime.stabilize_assembled_objects()
        except Exception as e:
            logger.warning("Grasp.execute weld failed for %s: %s", expected_item.name, e, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Grasp weld constraint failed: {type(e).__name__}: {e}",
                suggestion="Check that the object has not already been welded.",
            )
        logger.info(
            "Grasp.execute success: item=%s held_item=%s weld_pair=%s object_pose=%s",
            expected_item.name,
            self.runtime.held_item_name,
            self.runtime._weld_pair,
            _object_pose_summary(expected_item),
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

    TOOL_NAME = "Release"
    TOOL_DESCRIPTION = "Open the gripper and release the currently held Genesis workpiece."
    TOOL_PARAMETERS = {
        "expected_item": {
            "type": "str",
            "required": True,
            "description": "Genesis object name of the workpiece to release.",
            "resolver": "object",
        },
    }

    def __init__(self, runtime):
        super().__init__(runtime)

    def check(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        logger.info(
            "Release.check start: expected_item=%s held_item=%s weld_pair=%s",
            getattr(expected_item, "name", expected_item),
            self.runtime.held_item_name,
            self.runtime._weld_pair,
        )
        if not isinstance(expected_item, SceneObject):
            logger.warning("Release.check failed: invalid expected_item=%r", expected_item)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Release expected_item. Expected a SceneObject.",
            )
        if self.runtime.held_item_name != expected_item.name:
            held = self.runtime.held_item_name or "nothing"
            logger.warning(
                "Release.check failed: requested=%s held=%s weld_pair=%s",
                expected_item.name,
                held,
                self.runtime._weld_pair,
            )
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_GRIPPER_FAILURE,
                message=f"Cannot release '{expected_item.name}': gripper is holding '{held}'.",
                suggestion="Ensure the correct item was grasped before calling Release.",
            )
        logger.info("Release.check passed: item=%s", expected_item.name)
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Release pre-check passed for '{expected_item.name}'.",
        )

    @require_robot_active
    def execute(self, expected_item: SceneObject) -> SkillResult:
        return self._submit_to_controller(self._execute_body, expected_item)

    def _execute_body(self, expected_item: SceneObject) -> SkillResult:
        logger.info(
            "Release.execute start: expected_item=%s held_item=%s weld_pair=%s object_pose=%s",
            getattr(expected_item, "name", expected_item),
            self.runtime.held_item_name,
            self.runtime._weld_pair,
            _object_pose_summary(expected_item) if isinstance(expected_item, SceneObject) else None,
        )
        if not isinstance(expected_item, SceneObject):
            logger.warning("Release.execute failed: invalid expected_item=%r", expected_item)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid Release expected_item. Expected a SceneObject.",
            )
        if self.runtime._weld_pair is None or self.runtime.held_item_name != expected_item.name:
            held = self.runtime.held_item_name or "nothing"
            logger.warning(
                "Release.execute failed: requested=%s held=%s weld_pair=%s",
                expected_item.name,
                held,
                self.runtime._weld_pair,
            )
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
            snap_info = self.runtime.snap_object_to_place_target(expected_item.name)
            self.runtime.scene.step()
            self.runtime.stabilize_assembled_objects()
        except Exception as e:
            logger.warning("Release.execute weld removal failed for %s: %s", expected_item.name, e, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=self.runtime.get_current_state(),
                message=f"Release weld constraint removal failed: {type(e).__name__}: {e}",
            )
        logger.info(
            "Release.execute success: item=%s held_item=%s weld_pair=%s object_pose=%s snap=%s",
            expected_item.name,
            self.runtime.held_item_name,
            self.runtime._weld_pair,
            _object_pose_summary(expected_item),
            snap_info,
        )
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            robot_state=self.runtime.get_current_state(),
            message=f"Released '{expected_item.name}', weld constraint removed.",
            data={"released_item": expected_item.name, "placement_snap": snap_info},
        )

    def try_execute(self, expected_item: SceneObject, tool: Optional[object] = None) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(expected_item, tool)
            if not check.success:
                return check
        return self.execute(expected_item)
