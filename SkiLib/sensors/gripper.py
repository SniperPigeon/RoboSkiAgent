"""
Execution-time gripper perception sensors.

Each function in this module is a read-only observation tool for the LLM
Executor's ReAct loop.  They answer "what does the gripper currently hold?"
*after* a Grasp or Release action primitive has been called.

No class hierarchy, no check/execute/try_execute trinity.
Each public function is decorated with @tool for direct LangChain binding.

Simulated-failure injection
---------------------------
Call inject_failure() in tests to make all sensors in this module return
"nothing attached" on the *next* call, regardless of actual RoboDK state.

The flag is one-shot: it auto-resets after the first triggered call.
Injection is module-level (not per-function) so a single arm covers all
sensor queries within one logical test step.

Example:
    from SkiLib.sensors.gripper import inject_failure

    inject_failure()
    result = get_attachment_state()    # → grasp_confirmed=False (simulated)
    result = get_attachment_state()    # → real RoboDK state
"""

from typing import Optional
from langchain_core.tools import tool

from SkiLib.log import get_logger
from SkiLib.robotcontext import RobotContext

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Simulated-failure injection (test / debug only — never call in production)
# ---------------------------------------------------------------------------

_SIMULATED_FAILURE: bool = False


def inject_failure(enabled: bool = True) -> None:
    """
    Arm or disarm a one-shot sensor failure for the next sensor call.

    When armed, the next call to any sensor function in this module returns
    "nothing attached" regardless of actual RoboDK state, then disarms
    automatically.

    Args:
        enabled: True to arm (default), False to disarm without triggering.
    """
    global _SIMULATED_FAILURE
    _SIMULATED_FAILURE = enabled
    logger.warning(
        "GripperSensor: simulated failure injection %s.",
        "ARMED" if enabled else "DISARMED",
    )


def _consume_injection() -> bool:
    """
    Return True if a failure is armed and consume it (one-shot reset).
    Call once at the entry of each sensor function before any real query.
    """
    global _SIMULATED_FAILURE
    if _SIMULATED_FAILURE:
        _SIMULATED_FAILURE = False
        logger.warning("GripperSensor: simulated failure triggered and consumed.")
        return True
    return False


# ---------------------------------------------------------------------------
# Internal query helper (shared logic, not exposed as a tool)
# ---------------------------------------------------------------------------

def _ctx() -> RobotContext:
    ctx = RobotContext.instance()
    if ctx is None:
        raise RuntimeError("RobotContext is not initialized.")
    return ctx


def _query_attachment(tool_name: str) -> dict:
    """
    Query current gripper attachment state via RoboDK.

    Args:
        tool_name: If non-empty, query this specific tool; otherwise uses the
                   currently active tool via RobotContext.get_gripper_state().

    Returns a raw dict: {"active_tool": str, "grasped": list[str]}
    or {"error": str, "active_tool": str, "grasped": []} on bad tool_name.
    """
    ctx = _ctx()

    if tool_name:
        from robodk import robolink  # noqa: PLC0415
        tool = ctx.RDK.Item(tool_name)
        if not tool.Valid():
            return {
                "error": f"Tool '{tool_name}' not found in RoboDK station.",
                "active_tool": tool_name,
                "grasped": [],
            }
        grasped = [
            c.Name() for c in tool.Childs()
            if c.Type() == robolink.ITEM_TYPE_OBJECT
        ]
        return {"active_tool": tool_name, "grasped": grasped}

    # Use RobotContext helper (handles active-tool resolution + real/sim branch)
    state = ctx.get_gripper_state()
    return {
        "active_tool": state.get("active_tool") or "",
        "grasped": state.get("grasped", []),
    }


# ---------------------------------------------------------------------------
# LLM-callable sensor tools
# ---------------------------------------------------------------------------

@tool
def get_attachment_state(tool_name: str = "") -> dict:
    """
    Query what the gripper is currently holding.

    Call this after a Grasp action to confirm whether the pick succeeded.
    Returns the active tool name and a list of attached object names.

    Args:
        tool_name: RoboDK tool name to query.
                   Leave empty to use the currently active tool.

    Returns:
        grasp_confirmed (bool) — True if at least one object is attached.
        attached_items  (list) — names of all currently attached objects.
        active_tool     (str)  — name of the queried tool.
    """
    if _consume_injection():
        raw = _query_attachment(tool_name)
        return {
            "grasp_confirmed": False,
            "attached_items": [],
            "active_tool": raw.get("active_tool", tool_name),
            "_debug_simulated_failure": True,
        }

    raw = _query_attachment(tool_name)
    if "error" in raw:
        return {
            "grasp_confirmed": False,
            "attached_items": [],
            "active_tool": raw["active_tool"],
            "error": raw["error"],
        }

    return {
        "grasp_confirmed": bool(raw["grasped"]),
        "attached_items": raw["grasped"],
        "active_tool": raw["active_tool"],
    }


@tool
def is_item_grasped(item_name: str, tool_name: str = "") -> dict:
    """
    Verify that a specific named object is currently held by the gripper.

    Use this after Grasp when you need to confirm the *correct* part was
    picked — not just that something is attached, but the intended workpiece.

    Args:
        item_name: RoboDK object name of the expected workpiece.
        tool_name: RoboDK tool name to check against.
                   Leave empty to use the currently active tool.

    Returns:
        is_grasped  (bool) — True if item_name is attached to the tool.
        item_name   (str)  — echoes the queried item name.
        active_tool (str)  — name of the queried tool.
    """
    if _consume_injection():
        raw = _query_attachment(tool_name)
        return {
            "is_grasped": False,
            "item_name": item_name,
            "active_tool": raw.get("active_tool", tool_name),
            "_debug_simulated_failure": True,
        }

    raw = _query_attachment(tool_name)
    if "error" in raw:
        return {
            "is_grasped": False,
            "item_name": item_name,
            "active_tool": raw["active_tool"],
            "error": raw["error"],
        }

    return {
        "is_grasped": item_name in raw["grasped"],
        "item_name": item_name,
        "active_tool": raw["active_tool"],
    }


def get_tools() -> list:
    """Return all gripper sensor tools for LLM binding."""
    return [get_attachment_state, is_item_grasped]
