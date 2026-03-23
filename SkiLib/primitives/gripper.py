from SkiLib.base import (
    BasePrimitive, SkillResult, ExecutionPhase,
    ERROR_INVALID_PARAM, require_robot_active,
)
from robodk import robolink

from SkiLib.log import get_logger

logger = get_logger(__name__)

# Domain-specific error constants for gripper primitives
ERROR_GRASP_NO_OBJECT       = "GRASP_NO_OBJECT"        # AttachClosest found nothing in range
ERROR_GRASP_ALREADY_HOLDING = "GRASP_ALREADY_HOLDING"  # held_item already set (pre-check only)
ERROR_RELEASE_NOTHING_HELD  = "RELEASE_NOTHING_HELD"   # held_item is None (pre-check only)


def _get_context():
    """Return RobotContext, or raise RuntimeError if not yet initialized."""
    from SkiLib.robotcontext import RobotContext
    ctx = RobotContext.instance()
    if ctx is None:
        raise RuntimeError("RobotContext is not initialized.")
    return ctx


class Grasp(BasePrimitive):
    """
    Attach the nearest object to the robot's active tool (simulates gripper closing).

    RoboDK mechanism: tool.AttachClosest() re-parents the closest object to the
    tool item so it follows the TCP during subsequent moves.

    Parameters:
        force: Target gripping force in Newtons (0 = use gripper default).
               Logged in simulation; passed to gripper controller on real hardware.
        width: Target jaw width at grasp point in mm (0 = use gripper default).
               Useful for adaptive grippers; ignored for simple open/close grippers.
    """

    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item     = robot_object
        self.RDK:   robolink.Robolink = RDK_object

    def check(self, force: float = 0.0, width: float = 0.0) -> SkillResult:
        """
        Pre-check grasp pre-conditions.

        Validates:
          1. force and width are non-negative.
          2. A valid tool is attached to the robot.
          3. No object is already tracked as held.

        Note: returning success=False here does NOT prevent execute() from running.
        execute() is safe to call directly and handles state independently.
        """
        if force < 0.0:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=f"force must be >= 0 (got {force} N).",
                suggestion="Use force=0 to apply the gripper's default force.",
            )
        if width < 0.0:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=f"width must be >= 0 (got {width} mm).",
                suggestion="Use width=0 to apply the gripper's default jaw width.",
            )

        tool = self.robot.getLink(robolink.ITEM_TYPE_TOOL)
        if not tool.Valid():
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="No valid tool attached to the robot.",
                suggestion="Attach a gripper tool to the robot in RoboDK before grasping.",
            )

        ctx = _get_context()
        if ctx.held_item is not None:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_GRASP_ALREADY_HOLDING,
                message=f"Robot is already holding '{ctx.held_item.Name()}'. Release it first.",
                suggestion="Call Release.execute() before attempting another Grasp.",
            )

        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message="Grasp pre-conditions satisfied.",
        )

    @require_robot_active
    def execute(self, force: float = 0.0, width: float = 0.0) -> SkillResult:
        """
        Close the gripper: attach the nearest RoboDK object to the active tool.

        force: gripping force in N (0 = gripper default). Logged in simulation.
        width: jaw closing width in mm (0 = gripper default). Logged in simulation.

        Succeeds even if check() warned (e.g. already-holding state is overwritten).
        Fails only when AttachClosest() returns an invalid Item (nothing in range).
        """
        try:
            ctx = _get_context()
            tool = self.robot.getLink(robolink.ITEM_TYPE_TOOL)
            if not tool.Valid():
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.EXECUTION,
                    error_type=ERROR_INVALID_PARAM,
                    message="No valid tool attached to the robot.",
                    suggestion="Attach a gripper tool to the robot in RoboDK.",
                )

            if force > 0.0 or width > 0.0:
                logger.debug(
                    "Grasp parameters — force: %.1f N, width: %.1f mm "
                    "(simulation: logged only, not sent to hardware).",
                    force, width,
                )

            item = tool.AttachClosest()
            if not item.Valid():
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.EXECUTION,
                    error_type=ERROR_GRASP_NO_OBJECT,
                    message="AttachClosest() found no object within range.",
                    suggestion=(
                        "Verify the robot is positioned close enough to the target object. "
                        "RoboDK default tolerance is ~200 mm from the TCP."
                    ),
                    needs_hilp=True,
                )

            ctx.held_item = item
            logger.info(
                "Grasp: attached '%s' to tool '%s' (force=%.1f N, width=%.1f mm).",
                item.Name(), tool.Name(), force, width,
            )
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                message=f"Grasped '{item.Name()}' successfully.",
                data={"item_name": item.Name(), "force_n": force, "width_mm": width},
            )
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type="TIMEOUT",
                message=f"Grasp failed unexpectedly: {type(e).__name__}: {e}",
                suggestion="Check the RoboDK connection and station state.",
                needs_hilp=True,
            )

    def try_execute(self, force: float = 0.0, width: float = 0.0) -> SkillResult:
        check = self.check(force, width)
        if not check.success:
            return check
        return self.execute(force, width)  # type: ignore


class Release(BasePrimitive):
    """
    Detach the currently held object from the tool (simulates gripper opening).

    RoboDK mechanism: held_item.setParentStatic(station) re-parents the object
    back to the station root, preserving its current world-frame position.

    Parameters:
        width: Jaw opening width in mm after release (0 = fully open / gripper default).
               Logged in simulation; passed to gripper controller on real hardware.
    """

    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item     = robot_object
        self.RDK:   robolink.Robolink = RDK_object

    def check(self, width: float = 0.0) -> SkillResult:
        """
        Pre-check release pre-conditions.

        Validates:
          1. width is non-negative.
          2. held_item is currently set.

        Note: execute() proceeds even without a tracked held_item — the gripper
        opens regardless. This is intentional (e.g. safety open, state recovery).
        """
        if width < 0.0:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=f"width must be >= 0 (got {width} mm).",
                suggestion="Use width=0 for fully open (gripper default).",
            )

        ctx = _get_context()
        if ctx.held_item is None:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_RELEASE_NOTHING_HELD,
                message="No object is currently tracked as held.",
                suggestion=(
                    "Call Grasp.execute() first, or call Release.execute() directly "
                    "to open the gripper unconditionally."
                ),
            )

        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"Release pre-conditions satisfied (holding '{ctx.held_item.Name()}').",
        )

    @require_robot_active
    def execute(self, width: float = 0.0) -> SkillResult:
        """
        Open the gripper: detach the tracked object from the tool.

        width: jaw opening width in mm after release (0 = fully open). Logged in simulation.

        If no object is currently tracked (held_item is None), the call is a no-op
        success — the gripper opens even if nothing was formally grasped.
        """
        try:
            ctx = _get_context()

            if width > 0.0:
                logger.debug(
                    "Release parameters — width: %.1f mm "
                    "(simulation: logged only, not sent to hardware).",
                    width,
                )

            if ctx.held_item is None:
                logger.warning("Release.execute(): no held_item tracked; treating as no-op.")
                return SkillResult(
                    success=True,
                    execution_phase=ExecutionPhase.EXECUTION,
                    message="Gripper opened; no tracked object was held.",
                    data={"item_name": None, "width_mm": width},
                )

            item_name = ctx.held_item.Name()
            ctx.held_item.setParentStatic(self.RDK.ActiveStation())
            ctx.held_item = None

            logger.info(
                "Release: detached '%s' back to station root (width=%.1f mm).",
                item_name, width,
            )
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                message=f"Released '{item_name}' successfully.",
                data={"item_name": item_name, "width_mm": width},
            )
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type="TIMEOUT",
                message=f"Release failed unexpectedly: {type(e).__name__}: {e}",
                suggestion="Check the RoboDK connection and station state.",
                needs_hilp=True,
            )

    def try_execute(self, width: float = 0.0) -> SkillResult:
        check = self.check(width)
        if not check.success:
            return check
        return self.execute(width)  # type: ignore
