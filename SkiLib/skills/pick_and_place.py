"""
Pick and Place Skill - Platform-agnostic implementation

Architecture:
- NO RoboDK imports (platform-agnostic)
- Dependencies injected via primitives registry
- Pure composition of primitives
- Returns SkillResult throughout (CheckResult fully removed)
"""

from typing import Dict
from SkiLib.base import BaseSkill, SkillResult, ExecutionPhase
from SkiLib.log import get_logger

logger = get_logger(__name__)


class PickAndPlace(BaseSkill):
    """
    High-level pick and place skill.

    Sequence (execute):
        1. MoveJ  to pick_target
        2. Grasp
        3. MoveJ  to place_target
        4. Release
    """

    SKILL_DESCRIPTION   = "Pick an object from pick_target and place it at place_target."
    SKILL_CATEGORY      = "manipulation"
    REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL', 'Grasp', 'Release']

    def __init__(self, primitives: Dict):
        super().__init__(primitives)

    def check(self, pick_target, place_target, approach_height=100) -> SkillResult:
        """
        Check if pick and place is feasible.

        Args:
            pick_target:     Pick position (robolink.Item or joints).
            place_target:    Place position.
            approach_height: Unused — reserved for approach-offset logic.

        Returns:
            SkillResult — success=False with first failing check on failure.
        """
        pick_check = self.primitives['MoveJ'].check(target=pick_target)
        if not pick_check.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=pick_check.error_type,
                message=f"Cannot reach pick position: {pick_check.message}",
                suggestion=pick_check.suggestion,
                data=pick_check.data,
            )

        place_check = self.primitives['MoveJ'].check(target=place_target)
        if not place_check.success:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=place_check.error_type,
                message=f"Cannot reach place position: {place_check.message}",
                suggestion=place_check.suggestion,
                data=place_check.data,
            )

        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message="Pick and place pre-flight check passed.",
        )

    def execute(self, pick_target, place_target, approach_height=100) -> SkillResult:
        """
        Execute pick and place: MoveJ to pick → Grasp → MoveJ to place → Release.

        Args:
            pick_target:     Pick position.
            place_target:    Place position.
            approach_height: Unused — reserved for future MoveL approach offset.

        Returns:
            SkillResult — fails fast and returns on first primitive failure.
        """
        logger.info("Moving to pick position...")
        result = self.primitives['MoveJ'].execute(target=pick_target)
        if not result.success:
            return result

        logger.info("Grasping...")
        result = self.primitives['Grasp'].execute(item=pick_target)
        if not result.success:
            return result

        logger.info("Moving to place position...")
        result = self.primitives['MoveJ'].execute(target=place_target)
        if not result.success:
            return result

        logger.info("Releasing...")
        result = self.primitives['Release'].execute(item=pick_target)
        if not result.success:
            return result

        logger.info("Pick and place completed.")
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message="Pick and place completed successfully.",
        )

    def try_execute(self, pick_target, place_target, approach_height=100) -> SkillResult:
        """Run check(), then execute() if the check passed."""
        result = self.check(pick_target, place_target, approach_height)
        if not result.success:
            logger.warning("Pre-flight check failed: %s", result.message)
            return result
        return self.execute(pick_target, place_target, approach_height)
