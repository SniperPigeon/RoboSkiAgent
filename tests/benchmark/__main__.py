"""
CLI entry point for the benchmark suite.

Usage:
    python -m tests.benchmark                          # plan mode (score jsonl, no LLM/RoboDK)
    python -m tests.benchmark --mode plan-gen          # generate plans with env LLM, score
    python -m tests.benchmark --mode full              # plan-gen + execution (needs RoboDK)
    python -m tests.benchmark --mode executor          # executor_v2 node test (needs RoboDK)
    python -m tests.benchmark --save results.json

Model is always read from env (ROBOSKI_LLM_PROVIDER + ANTHROPIC_MODEL / OLLAMA_MODEL_ID).
"""
import argparse
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from trainer.apoptimizer.planning_agent import setup_robot_env

load_dotenv(override=True)


def _default_csv_path(mode: str) -> Path:
    provider = os.getenv("ROBOSKI_LLM_PROVIDER", "claude")
    if provider == "claude":
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    elif provider == "ollama":
        model = os.getenv("OLLAMA_MODEL_ID", "qwen3:latest")
    else:
        model = provider
    model_slug = model.replace(":", "-").replace("/", "-")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parents[1] / "result"
    out_dir.mkdir(exist_ok=True)
    return out_dir / f"{mode}_{timestamp}_{model_slug}.csv"

# Executor eval cases — params must match your live RoboDK scene
_EXECUTOR_CASES_DEFAULT = [
    {
        "case_id": "pick_place_part_a",
        "task_params": {
            "item":           "Part_A_1",
            "home_position":  "Home A",
            "pick_approach":  "App Pick Part A",
            "pick_target":    "Pick Part A",
            "place_approach": "App Place Part A",
            "place_target":   "Place Part A",
            "transit_motion": "MoveL",
            "initial_motion": "MoveL",
        },
        "verification": {
            "task_instruction": "Pick Part_A_1 and place it at Place Part A",
            "item_name":  "Part_A_1",
            "near_target": "Place Part A",
            "tolerance_mm": 100.0,
        },
    },
]


def _build_executor_cases():
    from SkiLib.verifiers.base import ItemExpectation, VerificationConfig
    from tests.benchmark.executor_eval import ExecutorEvalCase
    cases = []
    for c in _EXECUTOR_CASES_DEFAULT:
        v = c["verification"]
        cases.append(ExecutorEvalCase(
            case_id=c["case_id"],
            task_params=c["task_params"],
            verification=VerificationConfig(
                task_instruction=v["task_instruction"],
                expected_items=[ItemExpectation(
                    item_name=v["item_name"],
                    near_target=v["near_target"],
                    tolerance_mm=v["tolerance_mm"],
                )],
            ),
        ))
    return cases


def _setup_robot():
    from SkiLib.robotcontext import RobotContext
    from SkiLib.skill_loader import SkillMdLoader
    ctx = RobotContext()
    SkillMdLoader.instance()
    return ctx


def main():
    parser = argparse.ArgumentParser(description="RoboSkiAgent benchmark runner")
    parser.add_argument(
        "--mode", choices=["plan", "plan-gen", "full", "executor"], default="plan",
        help=(
            "plan: score existing plans from jsonl (no LLM/RoboDK) | "
            "plan-gen: generate plans with env LLM then score | "
            "full: plan-gen + execution outcome (needs RoboDK) | "
            "executor: executor_v2 node test (needs RoboDK)"
        ),
    )
    parser.add_argument("--save", default=None, metavar="FILE",
                        help="Save JSON report to FILE")
    parser.add_argument("--csv", default=None, metavar="FILE",
                        help="CSV output path (default: tests/result/<mode>_<time>_<model>.csv)")
    args = parser.parse_args()

    from tests.benchmark.csv_logger import CsvLogger
    from tests.benchmark.report import BenchmarkReport, print_report, save_report

    csv_path = Path(args.csv) if args.csv else _default_csv_path(args.mode)
    csv_log = CsvLogger(csv_path, mode=args.mode)

    if args.mode == "executor":
        setup_robot_env()
        from Agent.llm import create_llm
        from tests.benchmark.executor_eval import run_executor_eval
        llm = create_llm()
        report = BenchmarkReport()
        for case in _build_executor_cases():
            result = run_executor_eval(llm, case)
            report.executor_results.append(result)
            if csv_log:
                csv_log.log_executor(result)

    else:
        from tests.benchmark.runner import BenchmarkRunner
        from tests.benchmark.task_configs import load_task_configs, load_verifiable_tasks

        if args.mode == "full":
            setup_robot_env()
            tasks = load_verifiable_tasks()
        elif args.mode == "plan-gen":
            setup_robot_env()    # supervisor needs RobotContext for scene queries
            tasks = load_task_configs()
        else:  # plan — no RoboDK, no LLM
            tasks = load_task_configs()

        runner = BenchmarkRunner(tasks=tasks)
        report = runner.run(mode=args.mode, csv_logger=csv_log)  # type: ignore[arg-type]

    print_report(report)
    csv_log.close()
    print(f"\nResults saved to {csv_path}  (run_id={csv_log.run_id})")
    if args.save:
        save_report(report, args.save)


if __name__ == "__main__":
    main()
