"""
Execution-time placement verification sensor.

Used by the V2 executor's plan/check-step framework to verify that a workpiece
actually landed at the intended place target after Release.

Auto-discovered by SensorRegistry — no manual registration needed.

Distinction from metatools
--------------------------
metatools/ → planning-time symbolic queries (Supervisor: "what targets exist?")
sensors/   → execution-time physical queries (Executor: "did the part land here?")

get_object_position returns the XY distance to the nearest registered place target
and a boolean is_placed flag, making it suitable as a check_field="is_placed" step
in the V2 plan so the executor detects PLACEMENT_FAILURE before departing.
"""

from langchain_core.tools import tool

from SkiLib.log import get_logger
from SkiLib.robotcontext import RobotContext

logger = get_logger(__name__)


def _ctx() -> RobotContext:
    ctx = RobotContext.instance()
    if ctx is None:
        raise RuntimeError("RobotContext is not initialized.")
    return ctx


@tool
def get_object_position(item_name: str) -> dict:
    """Get the current placement status of a workpiece relative to registered place targets.

    Queries the Genesis physics state to determine whether the workpiece has landed
    within placement tolerance (5 cm XY) of the nearest place target after Release.

    Use as a check step after Release to confirm the workpiece is correctly placed:
        add_get_object_position_check(
            item_name=<item>,
            check_field="is_placed",
            check_expected=True,
            on_fail="llm_recovery",
        )

    Also callable directly during recovery to diagnose where the workpiece ended up.

    Args:
        item_name: Genesis scene object name (e.g. 'Gear_Small_1').

    Returns:
        {
            item:                           str,
            nearest_place_target:           str | None,
            xy_distance_to_nearest_place_m: float,        # XY-plane distance in metres
            z_offset_to_expected_m:         float,        # |object_z - expected_resting_z| in metres
            tilt_angle_deg:                 float | None, # disc tilt from horizontal; None if unreadable
            is_placed:                      bool,         # True if XY, Z, and tilt all within config limits
            description:                    str,          # human-readable summary with all three metrics
        }
    """
    try:
        return _ctx().get_object_position(item_name)
    except KeyError as e:
        logger.warning("get_object_position: object not found — %s", e)
        return {
            "item": item_name,
            "nearest_place_target": None,
            "xy_distance_to_nearest_place_m": float("inf"),
            "is_placed": False,
            "description": f"Object '{item_name}' not found in the Genesis scene.",
        }


def get_tools() -> list:
    """Return all placement sensor tools for SensorRegistry auto-discovery."""
    return [get_object_position]
