"""
plan_evaluator.py — Score planner output quality against APO reference plans.

Two modes:
  score_from_jsonl()       — score plans already in plan_claude.jsonl, no LLM needed.
  generate_and_score()     — run supervisor→planner with an LLM, then score.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from SkiLib.log import get_logger
from tests.benchmark.task_configs import TaskConfig

logger = get_logger(__name__)


@dataclass
class PlanScore:
    task_id: str
    model: str
    score: float
    expected_todo_list: list[dict]
    generated_todo_list: list[dict]


def score_from_jsonl(task: TaskConfig, model_label: str = "jsonl") -> PlanScore:
    """
    Score the plan already stored in task.expected_todo_list (no LLM call).
    Uses critic_score to compare it against itself — establishes data quality baseline.
    """
    from trainer.apoptimizer.planning_agent import critic_score
    score = critic_score(task.plan_input, task.expected_todo_list, task.expected_todo_list)
    logger.info("[plan_evaluator] %s | %s | score=%.2f (from jsonl)", model_label, task.task_id, score)
    return PlanScore(task_id=task.task_id, model=model_label, score=score,
                     expected_todo_list=task.expected_todo_list,
                     generated_todo_list=task.expected_todo_list)


def generate_and_score(llm: BaseChatModel, task: TaskConfig) -> PlanScore:
    """
    Run supervisor → planner with the given LLM, then score generated todo_list.
    RobotContext + SkillMdLoader must be initialized before calling.
    """
    from trainer.apoptimizer.planning_agent import build_planning_graph, critic_score, make_initial_state
    from Agent.nodes.supervisor import reset_supervisor_cache

    graph = build_planning_graph(llm=llm)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    reset_supervisor_cache()
    try:
        graph.invoke(make_initial_state(task.plan_input), config=config)  # type: ignore[arg-type]
    except Exception:
        logger.error("[plan_evaluator] graph failed for %s", task.task_id, exc_info=True)
        model_name = getattr(llm, "model", getattr(llm, "model_name", "unknown"))
        return PlanScore(task_id=task.task_id, model=model_name, score=0.0, generated_todo_list=[])

    generated = graph.get_state(config).values.get("todo_list", [])
    score = critic_score(task.plan_input, task.expected_todo_list, generated)
    model_name = getattr(llm, "model", getattr(llm, "model_name", "unknown"))
    logger.info("[plan_evaluator] %s | %s | score=%.2f (generated)", model_name, task.task_id, score)
    return PlanScore(task_id=task.task_id, model=model_name, score=score,
                     expected_todo_list=task.expected_todo_list,
                     generated_todo_list=generated)
