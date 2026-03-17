from robodk import robolink
from robodk.robolink import ITEM_TYPE_TOOL
from typing import Optional

from SkiLib.base import (
    BasePrimitive, SkillResult, ExecutionPhase, RobotState,
    ERROR_INVALID_PARAM, require_robot_active,
)
from SkiLib.log import get_logger

logger = get_logger(__name__)

# --- Module-level error type constants ---
# String constants (not an enum) so callers can import and compare without
# depending on this module's internal structure.
ERROR_ITEM_NOT_FOUND  = "ITEM_NOT_FOUND"   # provided Item reference is not valid in the station
ERROR_GRIPPER_FAILURE = "GRIPPER_FAILURE"  # gripper action failed (real robot: signal timeout / force fault)
ERROR_NO_ACTIVE_TOOL  = "NO_ACTIVE_TOOL"   # robot has no active tool and no tool Item was provided


def _snapshot_with_gripper(robot: robolink.Item, gripper_state: str) -> RobotState:
    """
    Capture current robot state and inject a gripper_state string.
    Returns RobotState with None joints/pose if the robot is unreachable.

    Unlike motion._snapshot(), gripper_state must be passed explicitly because
    RoboDK simulation does not provide gripper feedback via its API.
    """
    try:
        return RobotState(
            joints=list(robot.Joints()),
            pose=robot.Pose(),
            gripper_state=gripper_state,
        )
    except Exception:
        return RobotState(gripper_state=gripper_state)


def _resolve_tool(
    robot: robolink.Item,
    tool: Optional[robolink.Item],
    phase: ExecutionPhase,
) -> tuple[Optional[robolink.Item], Optional[SkillResult]]:
    """
    Return (tool_item, None) on success, or (None, error_result) on failure.
    Centralises tool-resolution logic shared by Grasp and Release.
    """
    if tool is not None:
        if not tool.Valid():
            return None, SkillResult(
                success=False,
                execution_phase=phase,
                error_type=ERROR_ITEM_NOT_FOUND,
                message="The provided tool Item is not valid in the RoboDK station.",
                suggestion="Verify the tool Item reference or omit tool to use the currently active tool.",
            )
        return tool, None

    active_tool = robot.getLink(ITEM_TYPE_TOOL)
    if not active_tool.Valid():
        return None, SkillResult(
            success=False,
            execution_phase=phase,
            error_type=ERROR_NO_ACTIVE_TOOL,
            message="No active tool is attached to the robot and no tool Item was provided.",
            suggestion="Attach a tool to the robot in RoboDK, or pass the tool Item explicitly.",
        )
    return active_tool, None


class Grasp(BasePrimitive):
    """
    Close the gripper and attach the nearest object to the tool tip.

    Simulation implementation: calls tool.AttachClosest() which uses RoboDK's
    proximity/collision detection to attach the nearest scene object to the tool.
    The caller (e.g. PickAndPlace) is responsible for positioning the robot at
    the pick target via MoveL before invoking Grasp — this ensures the nearest
    object is the intended part.

    Real robot extension points are marked with TODO comments inside execute().

    Args:
        item: The RoboDK Item of the part to grasp (used for pre-flight validity
              check and logged after attachment for traceability).
        tool: The RoboDK tool Item to use.  None = use the currently active tool.
    """

    def __init__(self, robot_object, RDK_object):
        super().__init__(robot_object, RDK_object)

    def check(
        self,
        item: robolink.Item,
        tool: Optional[robolink.Item] = None,
    ) -> SkillResult:
        # 1. Verify the target item is a valid reference
        if not item.Valid():
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_ITEM_NOT_FOUND,
                message="The provided item is not valid in the RoboDK station.",
                suggestion="Ensure the Item reference was obtained from the current RoboDK station.",
            )

        # 2. Verify tool availability
        _, tool_err = _resolve_tool(self.robot, tool, ExecutionPhase.PLANNING)
        if tool_err is not None:
            return tool_err

        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Grasp check passed: item '{item.Name()}' is valid and tool is available.",
        )

    @require_robot_active
    def execute(
        self,
        item: robolink.Item,
        tool: Optional[robolink.Item] = None,
    ) -> SkillResult:
        try:
            # Resolve tool (re-resolve independently of check; do not share state between calls)
            tool_item, tool_err = _resolve_tool(self.robot, tool, ExecutionPhase.EXECUTION)
            if tool_err is not None:
                return tool_err

            # --- Simulation: attach nearest object to tool tip ---
            # AttachClosest() uses RoboDK's proximity/collision detection to find
            # the nearest scene object and parents it to tool_item so the object
            # moves with the robot from this point on.
            # The caller must have already moved the TCP to the pick target via
            # MoveL — proximity guarantees the attached item is the intended part.
            attached = tool_item.AttachClosest()

            # TODO [Real robot]: replace or supplement the simulation block above with:
            #   self.robot.setDO(output_port, 1)           # send close signal
            #   _wait_for_gripper_closed(timeout_s=3.0)    # poll feedback DI
            #   _check_grip_force(min_force_n=...)         # optional force validation

            if not attached.Valid():
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.EXECUTION,
                    error_type=ERROR_GRIPPER_FAILURE,
                    robot_state=_snapshot_with_gripper(self.robot, "UNKNOWN"),
                    message=(
                        "AttachClosest() did not attach any object. "
                        "The TCP may not be close enough to the target item."
                    ),
                    suggestion="Ensure MoveL to the pick target completed successfully before calling Grasp.",
                )

            logger.info("Grasp: attached '%s' to tool '%s'.", attached.Name(), tool_item.Name())
            state = _snapshot_with_gripper(self.robot, gripper_state="CLOSED")
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                robot_state=state,
                message=f"Grasped '{attached.Name()}' successfully.",
                data={"item_name": attached.Name(), "tool_name": tool_item.Name()},
            )

        except Exception as e:
            logger.error("Grasp.execute raised %s.", type(e).__name__, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=_snapshot_with_gripper(self.robot, "UNKNOWN"),
                message=f"Grasp failed during execution: {type(e).__name__}",
                suggestion="Check the RoboDK station state and robot connection.",
            )

    def try_execute(
        self,
        item: robolink.Item,
        tool: Optional[robolink.Item] = None,
    ) -> SkillResult:
        check = self.check(item, tool)
        if not check.success:
            return check
        return self.execute(item, tool)


class Release(BasePrimitive):
    """
    Open the gripper and release all objects attached to the tool.

    Simulation implementation: calls tool.DetachAll(station) which detaches every
    object currently parented to the tool and re-parents them to the station root,
    preserving their world position.

    A real gripper opens its jaws mechanically and releases everything it holds —
    it cannot selectively retain one item while dropping another.  DetachAll
    mirrors this physical constraint and keeps simulation behaviour consistent
    with real-robot behaviour.

    Real robot extension points are marked with TODO comments inside execute().

    Args:
        item: The RoboDK Item of the part expected to be released (used for
              pre-flight validity check and logged for traceability only; the
              actual release always detaches all attached objects).
        tool: The RoboDK tool Item to use.  None = use the currently active tool.
    """

    def __init__(self, robot_object, RDK_object):
        super().__init__(robot_object, RDK_object)

    def check(
        self,
        item: robolink.Item,
        tool: Optional[robolink.Item] = None,
    ) -> SkillResult:
        # 1. Verify the target item is a valid reference
        if not item.Valid():
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_ITEM_NOT_FOUND,
                message="The provided item is not valid in the RoboDK station.",
                suggestion="Ensure the Item reference was obtained from the current RoboDK station.",
            )

        # 2. Advisory check: is the item currently attached to a tool?
        # Warning-only because check() is typically called during planning, before
        # Grasp has executed.  Blocking here would prevent pre-flight validation of
        # a full pick-and-place sequence.
        parent = item.Parent()
        if parent.Valid() and parent.Type() != ITEM_TYPE_TOOL:
            logger.warning(
                "Release.check: item '%s' parent is '%s' (type %d), not a tool. "
                "Expected if check() runs before Grasp; otherwise verify the sequence.",
                item.Name(), parent.Name(), parent.Type(),
            )

        # 3. Verify tool availability
        _, tool_err = _resolve_tool(self.robot, tool, ExecutionPhase.PLANNING)
        if tool_err is not None:
            return tool_err

        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Release check passed: item '{item.Name()}' is valid and tool is available.",
        )

    @require_robot_active
    def execute(
        self,
        item: robolink.Item,
        tool: Optional[robolink.Item] = None,
    ) -> SkillResult:
        try:
            tool_item, tool_err = _resolve_tool(self.robot, tool, ExecutionPhase.EXECUTION)
            if tool_err is not None:
                return tool_err

            # --- Simulation: detach all objects from tool, preserve world position ---
            # A real gripper opens its jaws and releases everything — it cannot
            # selectively retain one item while dropping another.  DetachAll()
            # mirrors this by re-parenting every child of tool_item to the station
            # root while keeping their world coordinates unchanged.
            tool_item.DetachAll(self.RDK.ActiveStation())

            # TODO [Real robot]: replace or supplement the simulation block above with:
            #   self.robot.setDO(output_port, 0)           # send open signal
            #   _wait_for_gripper_open(timeout_s=3.0)      # poll feedback DI
            #   _check_release_confirmation()              # optional sensor check

            logger.info(
                "Release: detached all objects from tool '%s' (expected item: '%s').",
                tool_item.Name(), item.Name(),
            )
            state = _snapshot_with_gripper(self.robot, gripper_state="OPEN")
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                robot_state=state,
                message=f"Released all objects from tool '{tool_item.Name()}' successfully.",
                # item.Name() retained for caller traceability even though all
                # attached objects were released.
                data={"item_name": item.Name(), "tool_name": tool_item.Name()},
            )

        except Exception as e:
            logger.error("Release.execute raised %s.", type(e).__name__, exc_info=True)
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_GRIPPER_FAILURE,
                robot_state=_snapshot_with_gripper(self.robot, "UNKNOWN"),
                message=f"Release failed during execution: {type(e).__name__}",
                suggestion="Check the RoboDK station state and robot connection.",
            )

    def try_execute(
        self,
        item: robolink.Item,
        tool: Optional[robolink.Item] = None,
    ) -> SkillResult:
        check = self.check(item, tool)
        if not check.success:
            return check
        return self.execute(item, tool)
