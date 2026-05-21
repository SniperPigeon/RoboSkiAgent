from SkiLib.base import BaseSkill, SkillResult, ExecutionPhase
from SkiLib.log import get_logger

logger = get_logger(__name__)


class DummySkill(BaseSkill):
    """A no-op skill used to verify SkillRegistry auto-discovery and as_tools() generation."""

    SKILL_DESCRIPTION = "Dummy skill for registry and tool-schema smoke testing."
    SKILL_CATEGORY    = "debug"
    REQUIRED_PRIMITIVES = []

    def __init__(self, primitives):
        super().__init__(primitives)
        logger.debug("DummySkill initialized with primitives: %s", list(primitives.keys()))

    def check(self, message: str = "hello") -> SkillResult:
        """Check that the dummy skill is reachable. message is echoed back in data."""
        logger.debug("DummySkill.check called with message=%r", message)
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"DummySkill.check passed. Echo: {message}",
            data={"echo": message},
        )

    def execute(self, message: str = "hello") -> SkillResult:
        """Execute the dummy skill. Does nothing except log and return success."""
        logger.debug("DummySkill.execute called with message=%r", message)
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message=f"DummySkill.execute completed. Echo: {message}",
            data={"echo": message},
        )

    def try_execute(self, message: str = "hello") -> SkillResult:
        """Run check then execute. Aborts on check failure."""
        logger.debug("DummySkill.try_execute called with message=%r", message)
        result = self.check(message)
        if not result.success:
            return result
        return self.execute(message)
