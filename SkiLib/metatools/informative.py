"""
Informative T-skills for the Supervisor agent.

These are read-only tools that expose symbolic scene information to the LLM.
Rules:
  - No coordinates, no matrices, no joint angles in return values.
  - All exceptions must be caught and returned as structured errors.
  - All return values use RoboDK symbolic names (strings) only.
"""

from typing import cast
from langchain_core.tools import tool
from robodk import robolink

from SkiLib.robotcontext import RobotContext


def _ctx() -> RobotContext:
    ctx = RobotContext.instance()
    if ctx is None:
        raise RuntimeError("RobotContext is not initialized.")
    return ctx


@tool
def list_targets() -> list[str]:
    """List all target names available in the RoboDK scene.
    Use this to discover valid pick/place/approach target names for task planning."""
    return cast(list[str], _ctx().RDK.ItemList(filter=robolink.ITEM_TYPE_TARGET, list_names=True))


@tool
def list_objects() -> list[str]:
    """List all object (workpiece) names in the RoboDK scene.
    Use this to discover which parts are present and available for manipulation."""
    return cast(list[str], _ctx().RDK.ItemList(filter=robolink.ITEM_TYPE_OBJECT, list_names=True))


@tool
def list_tools() -> list[str]:
    """List all tool names defined in the RoboDK scene.
    Use this to discover which end-effectors are available."""
    return cast(list[str], _ctx().RDK.ItemList(filter=robolink.ITEM_TYPE_TOOL, list_names=True))


@tool
def check_item_exists(name: str) -> bool:
    """Check whether an item with the given name exists in the RoboDK scene.
    Use this to validate target or object names before including them in a plan."""
    return _ctx().RDK.Item(name).Valid()


@tool
def get_gripper_state() -> dict:
    """Get the current gripper state: active tool name and list of grasped object names.
    Use this to determine whether the gripper is holding something before planning a pick."""
    return _ctx().get_gripper_state()


def get_tools() -> list:
    """Return all informative T-skills as a list for LLM tool binding."""
    return [list_targets, list_objects, list_tools, check_item_exists, get_gripper_state]
