"""
executor_v2 — Executor node that uses a bounded LLM sub-agent to sequence primitives.

Key difference from executor.py:
  - executor.py  : calls skill.try_execute() (Python), then LLM recovery on failure
  - executor_v2.py: immediately starts an LLM sub-agent that reads skill.md and
                    sequences MoveJ/MoveL/Grasp/Release primitives directly

The sub-agent has full responsibility for:
  1. Following the primitive sequence in skill.md
  2. Handling recoverable errors (e.g. re-approach → retry Grasp) without HITL
  3. Calling escalate_to_hitl when recovery is exhausted or the error mandates it

This design tests LLM instruction-following ability — the robot's execution path
is no longer determined by Python code but by LLM reasoning over skill.md.

post_task_router() is reused from executor.py (routing logic is identical).
"""

from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from Agent.state import GlobalState
from SkiLib.base import ERROR_ROBOT_INACTIVE, ExecutionPhase, SkillResult
from SkiLib.log import get_logger
from SkiLib.metatools.informative import get_gripper_state, list_targets
from SkiLib.robotcontext import RobotContext
from SkiLib.skill_loader import SkillMdLoader

# Re-export HITL escalation mechanism from original executor so graph_v2 can
# import everything from one place if needed.
from Agent.nodes.executor import escalate_tool, _EscalateHITLException  # noqa: F401

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def _build_executor_system_prompt(skill_name: str, skill_body: str, params: dict) -> str:
    """
    Build the sub-agent system prompt by combining:
      1. The base skill_executor.txt (symbol-name rules, escalate conditions)
      2. The skill.md body (execution sequence + recovery hints)
      3. The concrete parameter values resolved by the Planner

    The LLM sub-agent reads this prompt and calls MoveJ/MoveL/Grasp/Release
    in the sequence described by the skill body.
    """
    base = _load_prompt("skill_executor.txt")
    return (
        f"{base}\n\n"
        f"## Skill: {skill_name}\n\n"
        f"{skill_body}\n\n"
        "## Concrete Parameter Values for This Execution\n\n"
        "Replace all `{{parameter_name}}` placeholders in the sequence above with "
        "the following values:\n\n"
        + "\n".join(f"- `{k}` = `{v}`" for k, v in params.items())
    )


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

def executor_v2(state: GlobalState, *, llm: BaseChatModel) -> dict:
    """
    Executor node (V2): run a skill by spawning a bounded LLM sub-agent.

    The sub-agent reads skill.md and calls primitives (MoveJ, MoveL, Grasp,
    Release) directly.  On recoverable failure, the sub-agent retries per the
    skill's Recovery Hints without surfacing to the HITL Handler.  On
    unrecoverable failure, it calls escalate_to_hitl, which raises
    _EscalateHITLException and is caught here.

    State reads:  current_task, halt_flag
    State writes: last_result, current_task (cleared on success), halt_flag,
                  halt_reason, messages, execution_log
    """
    task       = state.get("current_task", {})
    tid        = task.get("task_id", "?")
    skill_name = task.get("skill", "")
    params     = task.get("params", {})

    # Halt guard — identical to original executor
    if state.get("halt_flag"):
        logger.warning("[executor_v2] Halted — skipping %s", tid)
        return {
            "execution_log": [f"[executor_v2] HALTED — skipping {tid}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_ROBOT_INACTIVE,
                message="Robot is halted. Executor skipped task.",
                needs_hitl=True,
            ),
        }

    # Resolve skill spec from skill.md
    loader = SkillMdLoader.instance()
    if not loader.has(skill_name):
        logger.error("[executor_v2] Unknown skill '%s' — not in skill.md library", skill_name)
        return {
            "execution_log": [f"[executor_v2] {tid} UNKNOWN SKILL: {skill_name}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type="SKILL_NOT_FOUND",
                message=f"Skill '{skill_name}' has no .md file in SkiLib/skills/.",
                suggestion="Add a skill.md file or use add_manual_task for unsupported operations.",
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    spec = loader.get(skill_name)
    logger.info("[executor_v2] Running: %s(%s) via sub-agent", skill_name, params)

    # Gather sub-agent tools: primitives + scene query + escalate
    ctx             = RobotContext.instance()
    primitive_tools = ctx.primitive_registry.as_tools()
    sub_agent_tools = [escalate_tool, *primitive_tools, list_targets, get_gripper_state]

    system_prompt = _build_executor_system_prompt(skill_name, spec.body, params)
    sub_agent     = create_agent(model=llm, tools=sub_agent_tools, system_prompt=system_prompt)

    try:
        sub_agent.invoke({
            "messages": [HumanMessage(
                content=(
                    f"Execute the {skill_name} skill now. "
                    f"Parameters: {params}. "
                    "Follow the execution sequence in your system prompt exactly."
                )
            )]
        })

    except _EscalateHITLException as e:
        logger.error("[executor_v2] %s escalated to HITL: %s", tid, e.error_type)
        return {
            "messages": [AIMessage(
                content=f"[{tid}] {skill_name} | FAILED | error: {e.error_type} | suggestion: {e.suggestion or 'N/A'}"
            )],
            "execution_log": [f"[executor_v2] {tid} {skill_name} -> ESCALATED: {e.error_type}"],
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

    # Sub-agent completed without escalating — treat as success
    # TODO: intercept ToolMessages to capture the final SkillResult from the last
    #       primitive call and surface it as last_result.data for richer logging.
    logger.info("[executor_v2] %s (%s) -> SUCCESS (sub-agent completed)", tid, skill_name)
    return {
        "messages": [AIMessage(content=f"[{tid}] {skill_name} | SUCCESS")],
        "execution_log": [f"[executor_v2] {tid} {skill_name} -> SUCCESS"],
        "last_result": SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message=f"{skill_name} completed by LLM sub-agent.",
        ),
        "current_task": {},   # vacate execution slot
    }


# ---------------------------------------------------------------------------
# Router — identical to executor.py's post_task_router
# ---------------------------------------------------------------------------

def post_task_router_v2(state: GlobalState) -> str:
    last_result: SkillResult | None = state.get("last_result")
    if last_result is None:
        logger.warning("[post_task_router_v2] last_result is None, routing to END")
        return "END"
    return "dispatcher" if last_result.success else "hitl_handler"
