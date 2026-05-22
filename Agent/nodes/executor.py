from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from SkiLib.base import ERROR_ROBOT_INACTIVE, ExecutionPhase, SkillResult
from SkiLib.log import get_logger
from SkiLib.metatools.informative import list_targets
from SkiLib.registry import SkillRegistry
from Agent.state import GlobalState
from Agent.llm import cached_system_message

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---- HITL escalation mechanism ------------------------------------------------
class _EscalateHITLException(Exception):
    def __init__(self, error_type: str, reason: str | None = None, suggestion: str | None = None):
        self.error_type = error_type
        self.reason     = reason
        self.suggestion = suggestion

    def __str__(self):
        return f"HITL escalation: {self.error_type} — {self.reason}"


def _escalate_to_hitl(error_type: str, reason: str | None = None, suggestion: str | None = None):
    """Escalate the current task to human intervention.

    Call this when the error is unrecoverable and automated recovery is not possible.
    Unrecoverable conditions include: hardware failure, IK/reachability failure,
    persistent collision, unknown skill parameters, or any situation where you
    are unsure how to proceed safely.

    Do NOT call this for transient or parameter errors you can fix by retrying
    with corrected arguments.

    Args:
        error_type:  Short error code describing the failure cause.
                     Use one of: 'IK_FAILURE', 'HARDWARE_ERROR', 'COLLISION',
                     'TIMEOUT', 'UNKNOWN_CAUSE', or any ERROR_* constant.
        reason:      Brief explanation of why the error occurred.
        suggestion:  Suggested action for the human supervisor to resolve the error.
    """
    raise _EscalateHITLException(error_type, reason, suggestion)


escalate_tool = StructuredTool.from_function(
    func=_escalate_to_hitl,
    name="escalate_to_hitl",
    description="Escalate the task to human intervention when recovery is not possible.",
    handle_tool_error=False,
)


# ---- Node function -------------------------------------------------------------
def executor(state: GlobalState, *, llm: BaseChatModel) -> dict:
    task       = state.get("current_task", {})
    tid        = task.get("task_id", "?")
    skill_name = task.get("skill", "?")

    if state.get("halt_flag"):
        logger.warning("[executor] Halted — skipping %s", tid)
        return {
            "execution_log": [f"[executor] HALTED — skipping {tid}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_ROBOT_INACTIVE,
                message="Robot is halted. Executor skipped task.",
                needs_hitl=True,
            ),
        }

    logger.info("[executor] Running: %s(%s)", skill_name, task.get("params"))

    registry = SkillRegistry.instance()
    skill    = registry.get_skill(skill_name)
    result: SkillResult = skill.try_execute(**task.get("params"))  # type: ignore

    if result.success:
        logger.info("[executor] %s (%s) -> SUCCESS", tid, skill_name)
        return {
            "messages":      [AIMessage(content=f"[{tid}] {skill_name} | SUCCESS")],
            "execution_log": [f"[executor] {tid} {skill_name} -> SUCCESS"],
            "last_result":   result,
            "current_task":  {},   # vacate execution slot
        }

    # First attempt failed — invoke LLM recovery loop
    logger.warning("[executor] %s failed: %s. Starting LLM recovery...", tid, result.error_type)

    executor_tools = [escalate_tool, *skill.as_tools(), list_targets]
    executor_agent = create_agent(
        model=llm,
        tools=executor_tools,
        system_prompt=cached_system_message(
            _load_prompt("executor.txt").format(
                error_info=result.to_llm_message()  # type: ignore
            )
        ),
    )

    try:
        executor_agent.invoke({"messages": [HumanMessage(content="Please analyze the failure and decide how to proceed.")]})
    except _EscalateHITLException as e:
        logger.error("[executor] %s escalated to HITL: %s", tid, e.error_type)
        return {
            "messages":      [AIMessage(content=f"[{tid}] {skill_name} | FAILED | error: {e.error_type} | suggestion: {e.suggestion or 'N/A'}")],
            "execution_log": [f"[executor] {tid} {skill_name} -> ESCALATED: {e.error_type}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=e.error_type,
                message=e.reason or "",
                suggestion=e.suggestion,
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    # LLM completed without escalating — assume recovery succeeded
    # TODO: intercept tool calls to capture exact SkillResult from retry
    logger.info("[executor] %s recovered by LLM.", tid)
    return {
        "messages":      [AIMessage(content=f"[{tid}] {skill_name} | RECOVERED")],
        "execution_log": [f"[executor] {tid} {skill_name} -> RECOVERED"],
        "last_result": SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message="Recovered by LLM retry.",
        ),
        "current_task": {},   # vacate execution slot
    }


def post_task_router(state: GlobalState) -> str:
    last_result: SkillResult | None = state.get("last_result")
    if last_result is None:
        logger.warning("[post_task_router] last_result is None, routing to END")
        return "END"
    return "dispatcher" if last_result.success else "hitl_handler"
