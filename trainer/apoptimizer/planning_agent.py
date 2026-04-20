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
    if _graph is not None and provider == _graph_provider:
        return _graph  # reuse cached graph if provider unchanged
    if _graph is not None and provider != _graph_provider:
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

Evaluate in this order:
  1. Count: does actual have the same number of tasks as expected?
     A missing or extra task is a SIGNIFICANT error regardless of what else is correct.
  2. Content: for each position, is the skill/type and key params correct?
  3. Order: are the tasks in the right sequence?

Scoring anchors:
  1.0 — identical or semantically equivalent (same count, same skills, same params, same order)
  0.8 — correct count and order, minor param differences only (e.g. slightly different wording
        in a manual description, or a non-critical param value difference)
  0.6 — one task missing OR one task extra OR one task in wrong position, rest correct
  0.4 — two or more tasks missing/extra/wrong, but core structure partially preserved
  0.2 — major structural errors: wrong skills, most tasks missing or in wrong order
  0.0 — completely wrong or empty

Rules for manual tasks (type="manual"):
  - Do NOT penalise wording differences. A manual task is correct if its description
    conveys the same operator action as the reference, even if phrased differently.
  - Only penalise a manual task if it is missing, misplaced in the sequence, or
    describes a fundamentally different action.
  - A missing manual task counts the same as a missing auto task — it is a real omission.

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
    except Exception:
        logger.error("[critic] failed — falling back to 0.0", exc_info=True)
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
    try:
        planner_prompt = prompt_template.format(**task)
    except KeyError as e:
        logger.warning("[rollout] prompt template has unresolvable variable %s — penalising with reward=0.0", e)
        return 0.0
    todo_list = planner_agent(task["plan_input"], planner_prompt)
    reward = critic_score(task["plan_input"], task["expected"], todo_list)

    logger.info(
        "[rollout] task=%s  plan=%d tasks  reward=%.2f",
        task["task_id"], len(todo_list), reward,
    )
    # Emit reward into the OTel tracer so agentlightning's runner log can display
    # "Final reward: X" correctly.  The runner (Case 1) captures trace_spans
    # *before* adding its own reward span to the store, so without this call
    # find_final_reward(trace_spans) always returns None.
    agl.emit_reward(reward)
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
    #
    # Assembly protocol
    # -----------------
    # The workpiece assembly ALWAYS follows the ABC rotation constraint:
    #   Step 1 (auto)  : Place Part A at target
    #   Step 2 (auto)  : Place Part B on top of Part A
    #   Step 3 (manual): Human flips the sub-assembly (MANDATORY between B and C)
    #   Step 4 (auto)  : Place Part C onto the flipped sub-assembly
    #
    # Additional manual steps (safety checks, fastening, quality inspection) may
    # appear at other points in the sequence, but the B→flip→C ordering is fixed.
    # Partial tasks (A only, A+B only) are also valid and require no flip.
    #
    # The instructions below are designed to:
    #   1. Cover all valid partial and full ABC sequences.
    #   2. Insert extra manual gates at different positions to stress-test whether
    #      the weak planner model places them correctly without duplicating the
    #      mandatory flip or reordering the ABC steps.
    #   3. Provide two full-cycle repetitions to test multi-step planning.

    instructions = [
        # ── Group 0: originals (kept for backwards compatibility) ──────────────
        "Place the first Part A at the target position.",
        "Place the first Part A at the target position, then place Part B on top.",
        "Place the first Part A at the target position, then place Part B on top of it at the target.",
        "Place the first Part A at the target, wait for human confirmation, then place Part B on top.",
        "Place Part A at the target, place Part B on top, then wait for the human operator to fasten screws and flip the workpiece, then place Part C.",

        # ── Group 1: full ABC, auto + mandatory flip, different phrasings ──────
        # Tests: can the model always insert exactly one manual flip between B and C?
        "Place the first Part A and the first Part B in sequence, wait for the operator to flip the workpiece, then place the first Part C to complete the assembly.",
        "Install the first Part A at the target, stack the first Part B on top, then after the manual flip place the first Part C.",
        "Complete a three-piece stacked assembly: place the first Part A, place the first Part B, manual workpiece flip, then place the first Part C.",

        # ── Group 2: full ABC + one extra manual step at different positions ───
        # Tests: correct placement of an additional manual gate without disrupting
        # the mandatory flip or the ABC order.

        # 2a. Safety check BEFORE any auto task
        "Before assembly, have the operator confirm safety conditions, then place the first Part A, place the first Part B, manual flip, finally place the first Part C.",

        # 2b. Alignment check AFTER A, before B
        "After placing the first Part A, pause for the operator to check alignment accuracy, then place the first Part B, flip the workpiece, then place the first Part C.",

        # 2c. Fastening BETWEEN A and B
        "Place the first Part A at the target, then after the operator tightens the locating pins place the first Part B, flip the workpiece, finally place the first Part C.",

        # 2d. Quality inspection AFTER C
        "Place the first Part A and the first Part B in sequence, after the manual workpiece flip place the first Part C, then wait for the operator to perform a final quality inspection.",

        # ── Group 3: full ABC + multiple extra manual steps ────────────────────
        # Tests: several manual gates without the model collapsing them or
        # confusing them with the mandatory flip.

        # 3a. Check after A, mandatory flip between B and C, final check after C
        "After placing the first Part A the operator inspects it, then place the first Part B, manual flip, place the first Part C, then perform a final quality inspection.",

        # 3b. Fastening after A, mandatory flip, final inspection after C
        "Place the first Part A, operator installs screws, place the first Part B, manual workpiece flip, place the first Part C, operator confirms product quality.",

        # ── Group 4: two full ABC cycles (stress test) ─────────────────────────
        # Each cycle contains the mandatory flip; between cycles a reset is needed.
        # Tests: six-step auto plan with two manual flips inserted correctly.

        # 4a. Two cycles back-to-back
        (
            "Complete two assembly cycles: Cycle 1 — place Part A, place Part B, manual flip, place Part C;"
            " after the operator resets the station, Cycle 2 — place Part A, place Part B, manual flip, place Part C."
        ),

        # 4b. Two cycles, each with a post-cycle quality check
        (
            "Cycle 1: place Part A, place Part B, manual flip, place Part C, operator QC inspection;"
            " Cycle 2: repeat the same steps — place Part A, place Part B, manual flip, place Part C, operator QC inspection."
        ),
    ]
    collect_dataset(instructions, output_path="collected_dataset.jsonl")