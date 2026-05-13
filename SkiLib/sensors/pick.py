"""
Execution-time pick pose sensor.

Computes a valid TCP pick pose for a workpiece based on its *current* physics
position and registers two temporary scene targets so MoveL can consume them
immediately in the recovery sub-agent loop.

Use during LLM recovery when a gear may have moved from its original staging
position (e.g. after a failed pick, or after a placement failure requiring
re-pick).  The nominal pick path continues to use static targets defined at
scene-build time — calling this tool on the happy path adds unnecessary IK
round-trips.

Registered target names follow the pattern:
    Dynamic_Pick_<item_name>           — precise grasp TCP waypoint (kind="pick")
    Dynamic_Pick_<item_name>_Approach  — approach waypoint above the pick (kind="approach")

Both targets are removed automatically by GenesisRuntime.reset() to prevent
cross-episode contamination.

Distinction from metatools
--------------------------
metatools/ → planning-time symbolic queries (no coordinates)
sensors/   → execution-time physical queries (can return and register coordinates)
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
def compute_pick_pose(item_name: str, grasp_profile: str = "default") -> dict:
    """Compute a pick pose for a workpiece from its current physics position.

    Reads the object's actual position from Genesis and registers two temporary
    scene targets that MoveL can use immediately:
        Dynamic_Pick_<item_name>          — grasp TCP waypoint
        Dynamic_Pick_<item_name>_Approach — approach waypoint above

    Call this during recovery when the gear may have moved from its staging
    area.  Pass the returned target names directly to MoveL as the `target`
    argument.

    Returns is_pickable=False (no targets registered) if the gear is tilted
    beyond PLACEMENT_TILT_TOL_DEG — call escalate_to_hitl in that case.

    Args:
        item_name: Genesis scene object name (e.g. 'Gear_Large_1').
        grasp_profile: Symbolic grasp profile, usually "default". Valid values
            are documented in SkiLib/genesis/assembly.md and validated by code.

    Returns:
        {
            item:                   str,
            pick_target_name:       str | None,  # pass to MoveL(target=...)
            approach_target_name:   str | None,  # pass to MoveL(target=...)
            obj_x:                  float,       # current object X in metres
            obj_y:                  float,       # current object Y in metres
            obj_z:                  float,       # current object Z in metres
            object_yaw_deg:         float | None,
            grasp_profile:          str | None,
            tcp_yaw_deg:            float | None,
            tilt_angle_deg:         float | None,
            is_pickable:            bool,
            description:            str,
        }
    """
    try:
        return _ctx().compute_pick_pose(item_name, grasp_profile)
    except KeyError as e:
        logger.warning("compute_pick_pose: object not found — %s", e)
        return {
            "item":                 item_name,
            "pick_target_name":     None,
            "approach_target_name": None,
            "obj_x":       None,
            "obj_y":       None,
            "obj_z":       None,
            "tilt_angle_deg": None,
            "is_pickable": False,
            "description": f"Object '{item_name}' not found in the Genesis scene.",
        }


def get_tools() -> list:
    """Return all pick sensor tools for SensorRegistry auto-discovery."""
    return [compute_pick_pose]
