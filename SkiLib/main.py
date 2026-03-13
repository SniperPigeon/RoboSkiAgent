import os, sys

# Handle import issues
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from SkiLib.robotcontext import RobotContext
from SkiLib.skills.pick_and_place import PickAndPlace

# ============= Initialize =============
context = RobotContext()

# ============= Use Primitives directly =============
MoveJ = context.primitives.get('MoveJ')   # Get MoveJ primitive

# ============= Use Skills (pass full registry) =============
pick_place = PickAndPlace(context.primitives)   # registry validates required primitives

if __name__ == "__main__":
    target = context.RDK.Item("App Pick Part A")
    place  = context.RDK.Item("App Place Part A")

    # Primitive直接使用
    result = MoveJ.check(target=target) # type: ignore
    print(result)

    # Skill组合使用
    pick_place.try_execute(target, place)