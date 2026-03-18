import os, sys

# Handle import issues
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from SkiLib.robotcontext import RobotContext
from SkiLib.registry import SkillRegistry

# ============= Initialize =============
context = RobotContext()  # also triggers SkillRegistry.set_robot_context() internally

# ============= Use Primitives directly =============
MoveJ = context.primitives.get('MoveJ')   # Get MoveJ primitive

# ============= Use Skills via SkillRegistry =============
skill_registry = SkillRegistry()
pick_place = skill_registry.get_skill('PickAndPlace')

if __name__ == "__main__":
    target = context.RDK.Item("App Pick Part A")
    place  = context.RDK.Item("App Place Part A")

    # Primitive直接使用
    result = MoveJ.check(target=target) # type: ignore
    print(result)

    # Skill组合使用
    pick_place.try_execute(target, place)