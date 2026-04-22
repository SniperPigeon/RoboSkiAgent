"""
planner_v2 — Planner node that derives tool schemas from skill.md files.

Drop-in replacement for planner.py.  The only behavioural difference is the
source of the add_<Skill>_task tool schemas:
  - planner.py   : schemas come from BaseSkill.as_tools() (Python class)
  - planner_v2.py: schemas come from SkillMdLoader (SkiLib/skills/*.md)

This means new skills can be added by writing a .md file only — no Python
class is required.  The Planner LLM also receives a "Skill Reference" section
in its system prompt (generated from skill.md bodies) so it knows what
parameters each skill needs.

The Planner still generates skill-level tasks (not primitive-level).
Primitive sequencing is done by the Executor V2 sub-agent at execution time.

Node signature is identical to planner():
    planner_v2(state: GlobalState, *, llm: BaseChatModel) -> dict
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from Agent.llm import get_node_timeouts
from Agent.state import GlobalState
from SkiLib.log import get_logger
from SkiLib.skill_loader import SkillMdLoader

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Load a prompt file as a plain string."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# Injection prompt, can be set via context var, used by Automated Prompt Optimization by Agent-Lightning
from contextvars import ContextVar
_prompt_override: ContextVar[str | None] = ContextVar("planner_prompt", default=None)


# ---------------------------------------------------------------------------
# Dynamic tool generation (from skill.md schemas)
# ---------------------------------------------------------------------------

def _make_planner_tools_v2() -> tuple[list[StructuredTool], list[dict]]:
    """
    Generate add_<SkillName>_task tools from SkillMdLoader specs.

    Each tool's args_schema is the Pydantic model built from the skill.md
    frontmatter parameters section.  The tool appends a task dict to the
    shared `plan` list when the LLM calls it.

    Also generates the always-present add_manual_task tool.

    Returns:
        tools: StructuredTool list for the Planner agent
        plan:  shared list that tools append to; becomes todo_list
    """
    plan: list[dict] = []
    tools: list[StructuredTool] = []

    loader = SkillMdLoader.instance()

    for skill_name, spec in loader.get_all().items():
        # Closure captures sname to avoid late-binding issues in the loop
        def _create_task_adder(sname: str):
            def _add_task(**kwargs) -> str:
                task_id = f"t{len(plan) + 1}"
                plan.append({
                    "task_id": task_id,
                    "type":    "auto",
                    "skill":   sname,
                    "params":  kwargs,
                })
                return f"Task {task_id} ({sname}) added. Plan so far: {len(plan)} task(s)."
            return _add_task

        tools.append(StructuredTool(
            name=f"add_{skill_name}_task",
            description=(
                f"Add a {skill_name} task to the plan. {spec.description} "
                "Fill ALL required parameters with exact symbol names from the scene."
            ),
            func=_create_task_adder(skill_name),
            args_schema=spec.args_schema,
        ))

    # Manual task tool — identical to original planner.py
    class AddManualTaskSchema(BaseModel):
        description: str = Field(description="What the human operator needs to do")

    def _add_manual(description: str) -> str:
        task_id = f"t{len(plan) + 1}"
        plan.append({"task_id": task_id, "type": "manual", "description": description})
        return f"Manual task {task_id} added."

    tools.append(StructuredTool.from_function(
        func=_add_manual,
        name="add_manual_task",
        description="Add a manual human-intervention step to the plan.",
        args_schema=AddManualTaskSchema,
    ))

    return tools, plan


# ---------------------------------------------------------------------------
# Skill reference block for system prompt
# ---------------------------------------------------------------------------

def _build_skill_reference() -> str:
    """
    Build a concise 'Skill Reference' block from all loaded skill.md specs.

    Injected into the Planner system prompt so the LLM understands what
    parameters each skill needs and what it does at a high level.
    Only the description and parameter list are included here — the full
    execution guide is used by the Executor sub-agent, not the Planner.
    """
    loader = SkillMdLoader.instance()
    specs  = loader.get_all()

    if not specs:
        return "(No skills loaded — use add_manual_task for all steps.)"

    lines: list[str] = []
    for name, spec in specs.items():
        param_list = ", ".join(spec.args_schema.model_fields.keys())
        lines.append(f"- **{name}**: {spec.description}\n  Parameters: {param_list}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

def planner_v2(state: GlobalState, *, llm: BaseChatModel) -> dict:
    """
    Planner node (V2): build skill-level todo_list via LLM tool calls.

    Differences from planner():
    - Tool schemas come from SkillMdLoader (skill.md) instead of SkillRegistry
    - System prompt includes a Skill Reference section listing available skills
    - No SkillRegistry dependency

    The Planner generates high-level skill tasks only.
    Primitive sequencing is handled by Executor V2 at execution time.

    State reads : messages (uses last message as task description from supervisor)
    State writes : todo_list, execution_log
    """
    if state.get("halt_flag"):
        logger.warning("[planner_v2] halt_flag set (reason: %s) — skipping planner", state.get("halt_reason"))
        return {"execution_log": [f"[planner_v2] skipped — {state.get('halt_reason')}"]}

    logger.info("[planner_v2] Building plan via tool calls...")

    tools, plan = _make_planner_tools_v2()

    # Build system prompt: load template from planner.txt, inject skill_ref
    # APO override bypasses the file and provides the full rendered string directly
    skill_ref = _build_skill_reference()
    override  = _prompt_override.get()
    base_rules = override or _load_prompt("planner.txt")
    system_prompt = (
        f"{base_rules}\n\n"
        "## Available Skills\n\n"
        "Use ONLY the skills listed below.  Each skill will be executed by a robot "
        "sub-agent that sequences the required primitives — you do not need to specify "
        "individual MoveL/Grasp steps, only the skill-level parameters.\n\n"
        f"{skill_ref}"
    )

    # Call LLM once and execute tool_calls sequentially to preserve generation order.
    # create_agent's ToolNode executes all calls in a single AIMessage in parallel,
    # causing non-deterministic task ordering with models that batch tool calls.
    tool_map = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    sup_content = state["messages"][-1].content
    messages    = [SystemMessage(content=system_prompt), HumanMessage(content=sup_content)]
    timeout     = get_node_timeouts()["planner"]

    pool   = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(llm_with_tools.invoke, messages)
    try:
        ai_msg = future.result(timeout=timeout)
    except FuturesTimeout:
        pool.shutdown(wait=False)
        logger.error("[planner_v2] LLM timed out after %ss", timeout)
        return {
            "todo_list": [],
            "execution_log": [f"[planner_v2] TIMEOUT after {timeout}s — plan aborted"],
        }
    finally:
        pool.shutdown(wait=False)

    for tc in getattr(ai_msg, "tool_calls", []):
        tool = tool_map.get(tc["name"])
        if tool is not None:
            tool.invoke(tc["args"])

    manual_count = sum(1 for t in plan if t["type"] == "manual")
    logger.info("[planner_v2] Done: %d tasks (%d manual)", len(plan), manual_count)

    return {
        "todo_list": plan,
        "execution_log": [
            f"[planner_v2] {len(plan)} tasks queued ({manual_count} manual): "
            + ", ".join(t.get("skill") or t.get("description", "?") for t in plan)
        ],
    }


# ---------------------------------------------------------------------------
# Helper: expose md-based skill list for supervisor
# ---------------------------------------------------------------------------

def get_available_skills_from_md() -> str:
    """
    Return a formatted string of skills loaded from skill.md files.
    Can be passed to supervisor._build_supervisor_prompt() as an alternative
    to the SkillRegistry-based skill list.
    """
    loader = SkillMdLoader.instance()
    specs  = loader.get_all()
    if not specs:
        return "  (no skills loaded)"
    return "\n".join(
        f"  - {name}: {spec.description} "
        f"(params: {', '.join(spec.args_schema.model_fields.keys())})"
        for name, spec in specs.items()
    )
