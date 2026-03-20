import os, sys
from time import sleep


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
    sleep(2)
    target = context.RDK.Item("App Pick Part A")
    place  = context.RDK.Item("App Place Part A")
    print(pick_place.as_tools())
    # Primitive直接使用
    from typing import cast
    from SkiLib.skills.pick_and_place import PickAndPlace
    pick_place = cast(PickAndPlace, skill_registry.get_skill('PickAndPlace'))
    pick_place.execute("Part_A_1","App Pick Part A", "Pick Part A", \
                       "App Place Part A", "Place Part A")
    #print(result)
    
    # Skill组合使用
    #pick_place.try_execute(target, place)