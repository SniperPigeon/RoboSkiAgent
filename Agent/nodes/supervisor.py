from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from Agent.llm import cached_system_message, get_node_timeouts
from Agent.state import GlobalState
from SkiLib.log import get_logger
from SkiLib.metatools.informative import get_tools as get_info_tools
from SkiLib.registry import SkillRegistry
from SkiLib.scenes.fmb import ASSEMBLY_REFERENCE_PATH

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_ASSEMBLY_REFERENCE = ASSEMBLY_REFERENCE_PATH


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _load_assembly_reference() -> str:
    try:
        return _ASSEMBLY_REFERENCE.read_text(encoding="utf-8")
    except OSError:
        return "(assembly reference unavailable)"


# ---- SupervisorOutput schema ---------------------------------------------------
class SceneSnapshot(BaseModel):
    """Symbolic scene state — no coordinates, only names."""
    targets: list[str] = Field(
        default_factory=list,
        description="All target names from the scene (pick/place/approach points).",
    )
    objects: list[str] = Field(
        default_factory=list,
        description="Workpiece names relevant to this task.",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="End-effector / tool names available.",
    )


class SupervisorOutput(BaseModel):
    """Fact sheet produced after knowledge saturation. Symbol-only, no coordinates."""
    task_intent_original: str = Field(description="Verbatim user instruction")
    task_intent: str = Field(
        description=(
            "Detailed step-by-step rewrite of the instruction using exact symbol names "
            "and skill names from the scene snapshot."
        )
    )
    scene: SceneSnapshot = Field(
        default_factory=SceneSnapshot,
        description="Populated from the pre-fetched scene snapshot in the system prompt.",
    )
    # available_skills: injected by code, not filled by LLM
    extra_info: str = Field(
        default="",
        description="Unresolvable ambiguities or free-text observations",
    )


def _get_available_skills() -> dict:
    """Pure-code: read skill signatures from SkillRegistry. No LLM involved."""
    registry = SkillRegistry.instance()
    if not registry:
        return {}
    return {
        name: registry.get_skill(name).execute.__doc__ or ""
        for name in registry.list_skills()
    }


def _build_scene_snapshot() -> str:
    """Pre-fetch scene info from RobotContext so the LLM always has it."""
    from SkiLib.robotcontext import RobotContext
    ctx = RobotContext.instance()
    if ctx is None:
        return "  (scene not initialized)"
    lines = [
        f"  Targets (pick/place/approach points): {ctx.list_targets()}",
        f"  Objects (graspable workpieces):       {ctx.list_objects()}",
        f"  Tools (end-effectors):                {ctx.list_tools()}",
    ]
    return "\n".join(lines)


def _build_supervisor_prompt() -> str:
    skills_text = "\n".join(
        f"  - {name}: {doc.strip()}"
        for name, doc in _get_available_skills().items()
    ) or "  (none registered)"
    return _load_prompt("supervisor.txt").format(
        skills_text=skills_text,
        scene_snapshot=_build_scene_snapshot(),
        assembly_reference=_load_assembly_reference(),
    )


# ---- Lazy agent cache (keyed by llm id to survive LLM hot-swap) ---------------
_agent_cache: dict[int, object] = {}


def _get_supervisor_agent(llm: BaseChatModel):
    key = id(llm)
    if key not in _agent_cache:
        _agent_cache[key] = create_agent(
            model=llm,
            tools=get_info_tools(),
            response_format=SupervisorOutput,
            system_prompt=cached_system_message(_build_supervisor_prompt()),
        )
        logger.info("[supervisor] Agent built. Skills: %s", list(_get_available_skills().keys()))
    return _agent_cache[key]


def reset_supervisor_cache() -> None:
    """Clear the agent cache (call before re-initialising SkillRegistry)."""
    _agent_cache.clear()


def supervisor_router(state: GlobalState) -> str:
    """Route to END on timeout/abort, otherwise proceed to planner."""
    return "END" if state.get("supervisor_action") == "abort" else "planner"


# ---- Node function -------------------------------------------------------------
def supervisor(state: GlobalState, *, llm: BaseChatModel) -> dict:
    logger.info("[supervisor] Starting knowledge saturation...")
    timeout = get_node_timeouts()["supervisor"]
    agent   = _get_supervisor_agent(llm)
    pool    = ThreadPoolExecutor(max_workers=1)
    future  = pool.submit(agent.invoke, {"messages": state["messages"]})
    try:
        result = future.result(timeout=timeout)
    except FuturesTimeout:
        pool.shutdown(wait=False)
        logger.error("[supervisor] LLM timed out after %ss", timeout)
        return {
            "execution_log":    [f"[supervisor] TIMEOUT after {timeout}s — aborting run"],
            "supervisor_action": "abort",
        }
    finally:
        pool.shutdown(wait=False)

    summary: SupervisorOutput | None = result.get("structured_response")
    if summary is None:
        raw = state["messages"][-1].content
        raw_str = raw if isinstance(raw, str) else str(raw)
        summary = SupervisorOutput(
            task_intent_original=raw_str,
            task_intent=raw_str,
            scene=SceneSnapshot(),
            extra_info="response_format unsupported — stub fallback",
        )
        logger.warning("[supervisor] structured_response missing, using stub fallback")

    output = summary.model_dump()
    output["available_skills"] = _get_available_skills()

    logger.info("[supervisor] Done. Intent: %s | Scene: %s", summary.task_intent, summary.scene)

    return {
        "messages":      [AIMessage(content=str(output))],
        "execution_log": [f"[supervisor] {summary.task_intent}"],
    }
