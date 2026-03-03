"""
Pick and Place Skill - Platform-agnostic implementation

This skill demonstrates the architecture principle:
- NO RoboDK imports (platform-agnostic)
- Dependencies injected through __init__
- Pure composition of primitives
- Easy to test (can mock primitives)
- Portable to other platforms (just swap primitive implementations)
"""

from SkiLib.base import BaseSkill, CheckResult, CheckResultLevel
from typing import Dict

class PickAndPlace(BaseSkill):
    """
    High-level pick and place skill.

    Required: MoveJ, MoveL
    Optional (future): Grasp, Release
    """

    REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL']

    def __init__(self, primitives: Dict):
        """
        Args:
            primitives: Full primitive registry dict (e.g. context.primitives).
                        Must contain 'MoveJ' and 'MoveL'.
        """
        super().__init__(primitives)
    
    def check(self, pick_target, place_target, approach_height=100):
        """
        Check if pick and place is feasible.
        
        Args:
            pick_target: Pick position (Item, joints, or pose)
            place_target: Place position
            approach_height: Approach height above target (mm)
        
        Returns:
            CheckResult with validation status
        """
        # Check 1: Can we reach pick position?
        pick_check = self.primitives['moveJ'].check(pick_target)
        if not pick_check.is_valid:
            return CheckResult(
                is_valid=False,
                message=f"Cannot reach pick position: {pick_check.message}",
                category="pick_reachability",
                level=CheckResultLevel.ERROR,
                details={"pick_check": pick_check.toDict()},
                suggestion="Verify pick target is within robot workspace"
            )
        
        # Check 2: Can we reach place position?
        place_check = self.primitives['moveJ'].check(place_target)
        if not place_check.is_valid:
            return CheckResult(
                is_valid=False,
                message=f"Cannot reach place position: {place_check.message}",
                category="place_reachability",
                level=CheckResultLevel.ERROR,
                details={"place_check": place_check.toDict()},
                suggestion="Verify place target is within robot workspace"
            )
        
        return CheckResult(
            is_valid=True,
            message="Pick and place is feasible",
            category="feasibility",
            level=CheckResultLevel.INFO
        )
    
    def execute(self, pick_target, place_target, approach_height=100):
        """
        Execute pick and place operation.
        
        Sequence:
            1. Move to approach position above pick
            2. Move down to pick (linear)
            3. Grasp
            4. Move up (linear)
            5. Move to approach position above place
            6. Move down to place (linear)
            7. Release
            8. Move up (linear)
        
        Args:
            pick_target: Pick position
            place_target: Place position
            approach_height: Safety approach height (mm)
        """
        # TODO: Calculate approach positions based on target + offset
        # For now, simplified version:
        
        # Phase 1: Pick
        print(f"[PickAndPlace] Moving to pick position...")
        self.primitives['moveJ'].execute(pick_target)
        
        # TODO: When grasp primitive available:
        # self.primitives['grasp'].execute()
        print(f"[PickAndPlace] Grasping... (TODO: implement grasp primitive)")
        
        # Phase 2: Place
        print(f"[PickAndPlace] Moving to place position...")
        self.primitives['moveJ'].execute(place_target)
        
        # TODO: When release primitive available:
        # self.primitives['release'].execute()
        print(f"[PickAndPlace] Releasing... (TODO: implement release primitive)")
        
        print(f"[PickAndPlace] ✓ Pick and place completed")
    
    def try_execute(self, pick_target, place_target, approach_height=100):
        """
        Safe execution: check first, then execute if valid.
        
        Returns:
            True if executed successfully, False otherwise
        """
        check_result = self.check(pick_target, place_target, approach_height)
        
        if check_result.is_valid:
            self.execute(pick_target, place_target, approach_height)
            return True
        else:
            print(f"[PickAndPlace] ✗ Execution aborted:")
            print(check_result.toPlainText())
            return False


# ============= Example Usage =============
if __name__ == "__main__":
    from SkiLib.robotcontext import RobotContext
    
    # Initialize context (auto-loads primitives)
    context = RobotContext()
    primitives = context.primitives
    
    # Create skill with dependency injection
    pick_place_skill = PickAndPlace(
        moveJ=primitives['MoveJ'],
        moveL=primitives.get('MoveL')  # May not exist yet
    )
    
    # Get targets
    pick = context.RDK.Item("App Pick Part A")
    place = context.RDK.Item("App Place Part A")
    
    # Execute
    pick_place_skill.try_execute(pick, place)
