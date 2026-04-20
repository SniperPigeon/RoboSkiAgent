"""
runner.py — BenchmarkRunner.

Modes:
  plan      — score existing plans from plan_claude.jsonl. No LLM, no RoboDK.
  plan-gen  — generate new plans with env-configured LLM, then score. No RoboDK.
  full      — plan-gen + full graph execution + TaskVerifier. Requires RoboDK.
"""
from __future__ import annotations

import uuid
from typing import Literal

from langchain_core.language_models import BaseChatModel

from SkiLib.RDK_Test import reset_station
from SkiLib.log import get_logger
from tests.benchmark.outcome_evaluator import evaluate_outcome
from tests.benchmark.plan_evaluator import generate_and_score, score_from_jsonl
from tests.benchmark.report import BenchmarkReport
from tests.benchmark.task_configs import TaskConfig

logger = get_logger(__name__)


class BenchmarkRunner:
    """
    Usage:
        tasks = load_task_configs()
        runner = BenchmarkRunner(tasks=tasks)
        report = runner.run(mode="plan")          # no LLM needed
        report = runner.run(mode="plan-gen")      # uses create_llm() from env
        report = runner.run(mode="full")          # needs RoboDK + create_llm()
    """

    def __init__(self, tasks: list[TaskConfig]):
        self.tasks = tasks

    def run(self, mode: Literal["plan", "plan-gen", "full"] = "plan",
            csv_logger=None) -> BenchmarkReport:
        report = BenchmarkReport()

        if mode == "plan":
            from tests.benchmark.report import print_plan_score
            logger.info("[runner] mode=plan  tasks=%d  (scoring from jsonl, no LLM)", len(self.tasks))
            print("\n=== Plan Quality (plan / from jsonl) ===")
            for task in self.tasks:
                score = score_from_jsonl(task)
                report.plan_scores.append(score)
                print_plan_score(score)
                if csv_logger:
                    csv_logger.log_plan(score)
            return report

        # plan-gen / full — need LLM from env
        from Agent.llm import create_llm
        llm = create_llm()
        model_name = getattr(llm, "model", getattr(llm, "model_name", "env-llm"))
        logger.info("[runner] mode=%s  model=%s  tasks=%d", mode, model_name, len(self.tasks))

        from tests.benchmark.report import print_plan_score
        print(f"\n=== Plan Quality ({mode}) ===")
        for task in self.tasks:
            score = generate_and_score(llm, task)
            report.plan_scores.append(score)
            print_plan_score(score)
            if csv_logger:
                csv_logger.log_plan(score)

            if mode == "full" and task.verification is not None:
                final_state = _run_full_graph(llm, task)
                report.outcome_scores.append(evaluate_outcome(model_name, task, final_state))

        return report


def _run_full_graph(llm: BaseChatModel, task: TaskConfig) -> dict:
    """Run the full graph (with executor) and return the final state dict."""
    from Agent.graph_v2 import build_graph_v2, make_initial_state
    from Agent.nodes.supervisor import reset_supervisor_cache
    from langgraph.errors import GraphInterrupt

    reset_station()
    graph = build_graph_v2(llm=llm)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    reset_supervisor_cache()
    try:
        graph.invoke(make_initial_state(task.plan_input), config=config)  # type: ignore[arg-type]
    except GraphInterrupt:
        # plan_review interrupt — auto-approve to continue execution
        graph.invoke({"plan_review_action": "approve"}, config=config)
    except Exception:
        logger.error("[runner] graph failed for %s", task.task_id, exc_info=True)

    return graph.get_state(config).values
