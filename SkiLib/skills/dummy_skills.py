from SkiLib.base import BaseSkill, SkillResult, ExecutionPhase


class DummySkill(BaseSkill):
    """A no-op skill used to verify SkillRegistry auto-discovery and as_tools() generation."""

    SKILL_DESCRIPTION = "Dummy skill for registry and tool-schema smoke testing."
    SKILL_CATEGORY    = "debug"
    REQUIRED_PRIMITIVES = []

    def __init__(self, primitives):
        super().__init__(primitives)
        print(f"[DummySkill] __init__ called. Received primitives: {list(primitives.keys())}")

    def check(self, message: str = "hello") -> SkillResult:
        """Check that the dummy skill is reachable. message is echoed back in data."""
        print(f"[DummySkill] check() called with message={message!r}")
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message=f"DummySkill.check passed. Echo: {message}",
            data={"echo": message},
        )

    def execute(self, message: str = "hello") -> SkillResult:
        """Execute the dummy skill. Does nothing except log and return success."""
        print(f"[DummySkill] execute() called with message={message!r}")
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message=f"DummySkill.execute completed. Echo: {message}",
            data={"echo": message},
        )

    def try_execute(self, message: str = "hello") -> SkillResult:
        """Run check then execute. Aborts on check failure."""
        print(f"[DummySkill] try_execute() called with message={message!r}")
        result = self.check(message)
        if not result.success:
            return result
        return self.execute(message)
