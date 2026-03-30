from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from SkiLib.log import get_logger
from SkiLib.metatools.informative import get_tools as get_info_tools
from SkiLib.registry import SkillRegistry
from Agent.state import GlobalState

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---- SupervisorOutput schema ---------------------------------------------------
class SupervisorOutput(BaseModel):
    """Fact sheet produced after knowledge saturation. Symbol-only, no coordinates."""
    task_intent_original: str = Field(description="Verbatim user instruction")
    task_intent: str = Field(
        description="Rewritten instruction using exact RoboDK symbol names"
    )
    scene: dict = Field(
        description="Keys: targets (list[str]), objects (list[str]), tools (list[str])"
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


def _build_supervisor_prompt() -> str:
    skills_text = "\n".join(
        f"  - {name}: {doc.strip()}"
        for name, doc in _get_available_skills().items()
    ) or "  (none registered)"
    return _load_prompt("supervisor.txt").format(skills_text=skills_text)


# ---- Lazy agent cache (keyed by llm id to survive LLM hot-swap) ---------------
_agent_cache: dict[int, object] = {}


def _get_supervisor_agent(llm: BaseChatModel):
    key = id(llm)
    if key not in _agent_cache:
        _agent_cache[key] = create_agent(
            model=llm,
            tools=get_info_tools(),
            response_format=SupervisorOutput,
            system_prompt=_build_supervisor_prompt(),
        )
        logger.info("[supervisor] Agent built. Skills: %s", list(_get_available_skills().keys()))
    return _agent_cache[key]


def reset_supervisor_cache() -> None:
    """Clear the agent cache (call before re-initialising SkillRegistry)."""
    _agent_cache.clear()


# ---- Node function -------------------------------------------------------------
def supervisor(state: GlobalState, *, llm: BaseChatModel) -> dict:
    logger.info("[supervisor] Starting knowledge saturation...")
    result = _get_supervisor_agent(llm).invoke({"messages": state["messages"]})

    summary: SupervisorOutput | None = result.get("structured_response")
    if summary is None:
        raw = state["messages"][-1].content
        raw_str = raw if isinstance(raw, str) else str(raw)
        summary = SupervisorOutput(
            task_intent_original=raw_str,
            task_intent=raw_str,
            scene={},
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
