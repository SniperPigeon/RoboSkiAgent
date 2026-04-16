"""
planning_agent.py — Supervisor + Planner graph wired for Agent Lightning APO.

Graph topology : START → supervisor → planner → END
Reward signal  : OpenAI GPT critic (gpt-4.1-mini)
APO target     : planner.txt prompt, injected via ContextVar
"""

if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

import ast
import json
import os
import uuid
from functools import partial
from typing import Any, TypedDict

from openai import OpenAI

import agentlightning as agl
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

load_dotenv()

from Agent.gui import _setup_env
from Agent.llm import create_llm
from Agent.nodes.planner_v2 import planner_v2, _prompt_override
from Agent.nodes.supervisor import supervisor, reset_supervisor_cache
from Agent.state import GlobalState
from SkiLib.log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Planning-only graph
# ---------------------------------------------------------------------------

def build_planning_graph(
    llm: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """supervisor → planner → END. No executor or interrupt nodes."""
    if llm is None:
        llm = create_llm()

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("SkiLib.base", "SkillResult"),
                ("SkiLib.base", "ExecutionPhase"),
            ]
        )
        checkpointer = MemorySaver(serde=serde)

    builder = StateGraph(GlobalState)
    builder.add_node("supervisor", partial(supervisor, llm=llm))
    builder.add_node("planner",    partial(planner_v2, llm=llm))
    builder.add_edge(START,        "supervisor")
    builder.add_edge("supervisor", "planner")
    builder.add_edge("planner",    END)
    return builder.compile(checkpointer=checkpointer)


def make_initial_state(prompt: str) -> dict:
    return {
        "messages":            [HumanMessage(content=prompt)],
        "todo_list":           [],
        "current_task":        {},
        "robot_state":         {},
        "halt_flag":           False,
        "halt_reason":         None,
        "last_result":         None,
        "plan_review_action":  None,
        "intervention_action": None,
        "hitl_command":        None,
        "execution_log":       [],
    }




# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

class PlannerTask(TypedDict):
    task_id:    str
    plan_input: str
    expected:   list[dict]  # expected task list, used by reward scorer (not here)


# ---------------------------------------------------------------------------
# Planning graph — singleton, shared across rollouts
# ---------------------------------------------------------------------------
# LangGraph compiled graphs are stateless: all run state lives in the
# checkpointer keyed by thread_id.  Sharing one compiled graph across
# concurrent calls is safe as long as each call uses a unique thread_id.

def setup_robot_env(debug_skip_check: bool = True) -> None:
    """Initialize RobotContext and SkillMdLoader before running the graph.

    Mirrors the setup performed by Agent/gui.py launch_gui(), adapted for the
    V2 path (SkillMdLoader instead of SkillRegistry).

    Args:
        debug_skip_check: Skip IK/collision checks (True for simulation/training).
    """
    from SkiLib.robotcontext import RobotContext
    from SkiLib.skill_loader import SkillMdLoader

    ctx = RobotContext()
    ctx.debug_skip_check = debug_skip_check
    SkillMdLoader.instance()
    logger.info("[setup] RobotContext + SkillMdLoader ready. debug_skip_check=%s", debug_skip_check)


_graph: Any = None
_graph_provider: str | None = None  # provider used to build current _graph


def _get_graph() -> Any:
    load_dotenv(override=True)  # re-read .env each call so provider changes take effect
    global _graph, _graph_provider
    provider = os.getenv("ROBOSKI_LLM_PROVIDER", "claude")
    if _graph is not None:
        logger.info("[planning_agent] LLM provider changed (%s → %s), rebuilding graph", _graph_provider, provider)
    setup_robot_env()
    _graph = build_planning_graph()
    _graph_provider = provider
    logger.info("[planning_agent] graph built (provider=%s)", provider)
    return _graph


# ---------------------------------------------------------------------------
# Core agent function
# ---------------------------------------------------------------------------

def planner_agent(human_input: str, planner_prompt: str) -> list[dict]:
    """Run supervisor → planner and return the generated todo_list.

    Concurrency contract
    --------------------
    - Each call uses a unique thread_id, so checkpointer state is fully
      isolated between concurrent invocations.
    - _prompt_override is a ContextVar: setting it only affects the current
      thread / asyncio task, so parallel calls never overwrite each other's
      prompt injection.

    Args:
        human_input:    Raw user instruction (e.g. "把 Part_A 移到目标").
        planner_prompt: Planner system-prompt text to inject for this run.
                        Comes from APO's PromptTemplate in the rollout.

    Returns:
        todo_list produced by planner_v2 (may be empty on failure).
    """
    graph  = _get_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    reset_supervisor_cache()

    # Inject prompt — ContextVar is isolated per thread/async context
    token = _prompt_override.set(planner_prompt)
    try:
        graph.invoke(make_initial_state(human_input), config=config)
    except Exception as e:
        logger.error("[planner_agent] graph failed: %s", e)
        return []
    finally:
        _prompt_override.reset(token)   # always restore, even on exception

    final_state = graph.get_state(config).values
    return final_state.get("todo_list", [])


# ---------------------------------------------------------------------------
# APO helper: initial prompt template seed
# ---------------------------------------------------------------------------

_PROMPTS_DIR = (
    __import__("pathlib").Path(__file__).resolve().parent.parent.parent
    / "Agent" / "prompts"
)


def get_initial_planner_prompt_template() -> agl.PromptTemplate:
    """Return the planner.txt content as the initial PromptTemplate for APO.

    APO starts optimisation from this seed and mutates the base-rules text.
    The ``skill_ref`` block (Available Skills section) is NOT part of the
    optimised template — it is always appended at runtime by planner_v2 so
    the template stays model-agnostic and scene-agnostic.
    """
    raw = (_PROMPTS_DIR / "planner.txt").read_text(encoding="utf-8")
    return agl.PromptTemplate(template=raw, engine="f-string")


# ---------------------------------------------------------------------------
# OpenAI critic
# ---------------------------------------------------------------------------

_CRITIC_SYSTEM = """\
You are an expert evaluator for a robotic assembly planner.
You will be given:
  - instruction: the natural-language assembly task
  - expected: the reference todo_list produced by an expert
  - actual:   the todo_list produced by the model under evaluation

Score the actual plan on a scale from 0.0 to 1.0:
  1.0 — identical or semantically equivalent (same skills, same symbol names, same order)
  0.7 — correct skills and order, minor param differences (e.g. different approach height)
  0.4 — partially correct (some tasks right, some missing or wrong)
  0.0 — completely wrong or empty

Respond with ONLY a JSON object: {"score": <float>, "reason": "<one sentence>"}
"""

_critic_client: OpenAI | None = None


def _get_critic_client() -> OpenAI:
    global _critic_client
    if _critic_client is None:
        _critic_client = OpenAI()
    return _critic_client


def critic_score(plan_input: str, expected: list[dict], actual: list[dict]) -> float:
    """Call GPT-4.1-mini to score the actual todo_list against the expected one.

    Returns a float in [0.0, 1.0].  Falls back to 0.0 on any API error so
    that a single bad rollout never crashes the training run.
    """
    user_content = json.dumps({
        "instruction": plan_input,
        "expected":    expected,
        "actual":      actual,
    }, ensure_ascii=False, indent=2)

    try:
        response = _get_critic_client().chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=256,
            messages=[
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user",   "content": user_content},
            ],
        )
        text = response.choices[0].message.content or ""
        data = json.loads(text)
        score = float(data["score"])
        logger.debug("[critic] score=%.2f  reason=%s", score, data.get("reason", ""))
        return max(0.0, min(1.0, score))
    except Exception as e:
        logger.error("[critic] failed: %s", e)
        return 0.0


# ---------------------------------------------------------------------------
# Agent Lightning rollout
# ---------------------------------------------------------------------------

@agl.rollout
def planner_rollout(task: PlannerTask, prompt_template: agl.PromptTemplate) -> float:
    """Agent Lightning rollout entry point.

    1. Render the APO-optimised planner prompt
    2. Run supervisor → planner → todo_list
    3. Score todo_list against task["expected"] via Claude Sonnet critic
    4. Return reward in [0.0, 1.0]
    """
    planner_prompt = prompt_template.format(**task)
    todo_list      = planner_agent(task["plan_input"], planner_prompt)
    reward         = critic_score(task["plan_input"], task["expected"], todo_list)

    logger.info(
        "[rollout] task=%s  plan=%d tasks  reward=%.2f",
        task["task_id"], len(todo_list), reward,
    )
    return reward


# ---------------------------------------------------------------------------
# Dataset collection
# ---------------------------------------------------------------------------

def _validate_todo_list(todo_list: list[dict], valid_skills: set[str]) -> tuple[bool, str]:
    """
    Structural validation for a generated todo_list.

    Checks:
    - At least one task was produced.
    - Every auto task names a registered skill.
    - Every auto task has a non-empty params dict.
    - Every manual task has a non-empty description.

    Returns (ok, reason).  reason is empty string when ok is True.
    """
    if not todo_list:
        return False, "empty todo_list"

    for t in todo_list:
        if t.get("type") == "auto":
            skill = t.get("skill", "")
            if skill not in valid_skills:
                return False, f"unknown skill '{skill}'"
            if not t.get("params"):
                return False, f"task {t.get('task_id')} has empty params"
        elif t.get("type") == "manual":
            if not t.get("description", "").strip():
                return False, f"task {t.get('task_id')} has empty description"
        else:
            return False, f"task {t.get('task_id')} has unknown type '{t.get('type')}'"

    return True, ""


def collect_dataset(
    instructions: list[str],
    output_path: str = "dataset.jsonl",
    *,
    skip_invalid: bool = True,
) -> list[PlannerTask]:
    """
    Run planner_agent on each instruction using the current planner.txt as the
    prompt (Sonnet by default via create_llm), validate the resulting todo_list,
    and write accepted samples to a JSONL file.

    Args:
        instructions:  List of natural-language assembly instructions.
        output_path:   Destination file; each line is a JSON-serialised PlannerTask.
        skip_invalid:  When True, invalid samples are logged and skipped.
                       When False, raises ValueError on the first invalid sample.

    Returns:
        List of accepted PlannerTask dicts (mirrors what was written to disk).
    """
    import json
    from SkiLib.skill_loader import SkillMdLoader

    prompt       = get_initial_planner_prompt_template().format()
    valid_skills = set(SkillMdLoader.instance().get_all().keys())
    accepted: list[PlannerTask] = []

    with open(output_path, "w", encoding="utf-8") as fout:
        for idx, instruction in enumerate(instructions):
            task_id = f"collect_{idx:04d}"
            logger.info("[collect] %s: %s", task_id, instruction[:80])

            todo_list = planner_agent(instruction, prompt)

            ok, reason = _validate_todo_list(todo_list, valid_skills)
            if not ok:
                msg = f"[collect] {task_id} rejected — {reason}"
                if skip_invalid:
                    logger.warning(msg)
                    continue
                raise ValueError(msg)

            sample: PlannerTask = {
                "task_id":    task_id,
                "plan_input": instruction,
                "expected":   todo_list,
            }
            fout.write(json.dumps(sample, ensure_ascii=False) + "\n")
            fout.flush()
            accepted.append(sample)
            logger.info("[collect] %s accepted (%d tasks)", task_id, len(todo_list))

    logger.info("[collect] done: %d/%d accepted → %s", len(accepted), len(instructions), output_path)
    return accepted

if __name__ == "__main__":
    # Example usage: collect dataset from hardcoded instructions list

    instructions = [
        "把第一个Part A放到目标位置。",
        "把第一个Part A放到目标位置，然后把Part B放上去。",
        "把第一个Part A放到目标位置，然后把Part B放到它上面，即目标位置。",
        "把第一个Part A放到目标，然后等人类确认后，把Part B放到它上面。",
        "把Part A放到目标位置，把Part B放到它上面，等待人类操作员帮你上螺丝并翻转工件后，你把Part C放上去。",
    ]
    collect_dataset(instructions, output_path="collected_dataset.jsonl")