import os, sys
from time import sleep
# Handle import issues
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from SkiLib.registry import SkillRegistry



from robodk import robolink
from SkiLib.robotcontext import RobotContext

# ============= Initialize =============
context = RobotContext()
RDK   = context.RDK
robot = context.robot
skill_registry = SkillRegistry.instance()

if __name__ == "__main__":

    # # ── Scene Enumeration ─────────────────────────────────────────────────────
    # print("=== ItemList by type ===")
    # print("ROBOT:",   RDK.ItemList(filter=robolink.ITEM_TYPE_ROBOT,   list_names=True))
    # print("TARGET:",  RDK.ItemList(filter=robolink.ITEM_TYPE_TARGET,  list_names=True))
    # print("FRAME:",   RDK.ItemList(filter=robolink.ITEM_TYPE_FRAME,   list_names=True))
    # print("TOOL:",    RDK.ItemList(filter=robolink.ITEM_TYPE_TOOL,    list_names=True))
    # print("OBJECT:",  RDK.ItemList(filter=robolink.ITEM_TYPE_OBJECT,  list_names=True))
    # print("PROGRAM:", RDK.ItemList(filter=robolink.ITEM_TYPE_PROGRAM, list_names=True))

    # # ── Single Item Metadata ──────────────────────────────────────────────────
    # print("\n=== Item metadata: 'App Pick Part A' ===")
    # item = RDK.Item("App Pick Part A")
    # print("Valid:",  item.Valid())
    # print("Type:",   item.Type())
    # print("Parent:", item.Parent().Name())
    # print("Pose (relative to parent):\n", item.Pose())
    # print("Pose (world/absolute):\n",     item.PoseAbs())
    # print("Children:", [c.Name() for c in item.Childs()])

    # # ── Robot State ───────────────────────────────────────────────────────────
    # print("\n=== Robot state ===")
    # print("Name:",         robot.Name())
    # print("Joints:",       robot.Joints())
    # print("Pose (TCP):\n", robot.Pose())
    # print("PoseFrame:\n",  robot.PoseFrame())
    # print("PoseTool:\n",   robot.PoseTool())
    # print("ActiveTool:",   robot.getLink(robolink.ITEM_TYPE_TOOL).Name())
    # print("JointLimits:",  robot.JointLimits())

    # # ── Tool / Gripper State ──────────────────────────────────────────────────
    # # RoboDK has no native open/closed API; grasped objects become children of the tool.
    # print("\n=== Tool / Gripper state ===")
    # tool = robot.getLink(robolink.ITEM_TYPE_TOOL)
    # print("Active tool:", tool.Name())
    # print("Tool children (= currently grasped objects):", [c.Name() for c in tool.Childs()])

    # # Cross-check from the object side: if object's Parent() == tool, it's grasped
    # obj = RDK.Item("Part_A_1")
    # if obj.Valid():
    #     parent = obj.Parent()
    #     print(f"Part_A_1 parent: {parent.Name()} (type={parent.Type()})")
    #     print(f"Part_A_1 is grasped by active tool: {parent.Name() == tool.Name()}")
        
    # ── Informative T-skills (via metatools) ──────────────────────────────────
    print("\n=== Informative T-skills ===")
    from SkiLib.metatools.informative import (
        list_targets, list_objects, list_tools, check_item_exists, get_gripper_state
    )
    print("list_targets():",        list_targets.invoke({}))
    print("list_objects():",        list_objects.invoke({}))
    print("list_tools():",          list_tools.invoke({}))
    print("check_item_exists('App Pick Part A'):", check_item_exists.invoke({"name": "App Pick Part A"}))
    print("check_item_exists('NonExistent'):",     check_item_exists.invoke({"name": "NonExistent"}))
    print("get_gripper_state():",   get_gripper_state.invoke({}))
    
    print(SkillRegistry.instance().get_skill('PickAndPlace').as_tools())

    # from typing import cast
    # from SkiLib.skills.pick_and_place import PickAndPlace
    # pick_place = cast(PickAndPlace, skill_registry.get_skill('PickAndPlace'))
    # pick_place.execute("Part_A_1","App Pick Part A", "Pick Part A", \
    #                    "App Place Part A", "Place Part A")