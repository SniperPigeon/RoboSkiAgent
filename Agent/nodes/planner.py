from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Annotated, Literal, Union

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from Agent.llm import get_node_timeouts
from Agent.state import GlobalState
from SkiLib.log import get_logger
from SkiLib.registry import SkillRegistry

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---- Task models ---------------------------------------------------------------
class AutoTask(BaseModel):
    task_id: str
    type: Literal["auto"] = "auto"
    skill: str
    params: dict


class ManualTask(BaseModel):
    task_id: str
    type: Literal["manual"] = "manual"
    description: str


Task = Annotated[Union[AutoTask, ManualTask], Field(discriminator="type")]


class PlannerOutput(BaseModel):
    todo_list: list[Task]


# ---- Dynamic tool generation ---------------------------------------------------
def _make_planner_tools(registry) -> tuple[list[StructuredTool], list[dict]]:
    """
    For each Skill, generate an add_<SkillName>_task tool reusing try_execute's
    args_schema. task_id is auto-assigned; LLM only fills skill parameters.
    """
    plan: list[dict] = []
    tools: list[StructuredTool] = []

    for skill_name in (registry.list_skills() if registry else []):
        skill = registry.get_skill(skill_name)
        try_exec = next(
            (t for t in skill.as_tools() if t.name.endswith("_try_execute")), None
        )
        if try_exec is None or try_exec.args_schema is None:
            continue

        def _create_task_adder(sname: str):
            def _add_task(**kwargs) -> str:
                task_id = f"t{len(plan) + 1}"
                plan.append({"task_id": task_id, "type": "auto", "skill": sname, "params": kwargs})
                return f"Task {task_id} ({sname}) added. Plan so far: {len(plan)} task(s)."
            return _add_task

        tools.append(StructuredTool(
            name=f"add_{skill_name}_task",
            description=f"Add a {skill_name} task. " + (try_exec.description or "").splitlines()[0],
            func=_create_task_adder(skill_name),
            args_schema=try_exec.args_schema,
        ))

    # Manual task tool
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


# ---- Node function -------------------------------------------------------------
def planner(state: GlobalState, *, llm: BaseChatModel) -> dict:
    logger.info("[planner] Building plan via tool calls...")
    registry = SkillRegistry.instance()
    tools, plan = _make_planner_tools(registry)

    agent = create_agent(model=llm, tools=tools, system_prompt=_load_prompt("planner.txt"))

    # Supervisor output is the last AIMessage; wrap as HumanMessage for Anthropic
    sup_content = state["messages"][-1].content
    timeout = get_node_timeouts()["planner"]
    pool    = ThreadPoolExecutor(max_workers=1)
    future  = pool.submit(agent.invoke, {"messages": [HumanMessage(content=sup_content)]})
    try:
        future.result(timeout=timeout)
    except FuturesTimeout:
        pool.shutdown(wait=False)
        logger.error("[planner] LLM timed out after %ss", timeout)
        return {
            "todo_list": [],
            "execution_log": [f"[planner] TIMEOUT after {timeout}s — plan aborted"],
        }
    finally:
        pool.shutdown(wait=False)

    manual_count = sum(1 for t in plan if t["type"] == "manual")
    logger.info("[planner] Done: %d tasks (%d manual)", len(plan), manual_count)

    return {
        "todo_list":     plan,
        "execution_log": [
            f"[planner] {len(plan)} tasks queued ({manual_count} manual): "
            + ", ".join(t.get("skill") or t.get("description", "?") for t in plan)
        ],
    }
