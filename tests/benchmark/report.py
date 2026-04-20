"""
report.py — Aggregate and display benchmark results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Optional

from tests.benchmark.executor_eval import ExecutorEvalResult
from tests.benchmark.outcome_evaluator import OutcomeScore
from tests.benchmark.plan_evaluator import PlanScore


@dataclass
class BenchmarkReport:
    plan_scores: list[PlanScore] = field(default_factory=list)
    outcome_scores: list[OutcomeScore] = field(default_factory=list)
    executor_results: list[ExecutorEvalResult] = field(default_factory=list)


def _group_by_model(items, key="model"):
    groups: dict[str, list] = {}
    for item in items:
        m = getattr(item, key)
        groups.setdefault(m, []).append(item)
    return groups


def _fmt_task(task: dict) -> str:
    """One-line summary of a todo_list entry."""
    if task.get("type") == "manual":
        desc = task.get("description", "")[:40]
        return f"MANUAL: {desc}"
    skill = task.get("skill", "?")
    params = task.get("params", {})
    if skill == "PickAndPlace":
        return f"{skill}({params.get('item','?')} → {params.get('place_target','?')})"
    return f"{skill}({', '.join(f'{k}={v}' for k, v in params.items())})"


def _print_plan_diff(expected: list[dict], actual: list[dict]) -> None:
    """Print expected and actual todo_lists side by side."""
    col = 48
    header = f"  {'EXPECTED':<{col}}  ACTUAL"
    print(header)
    print("  " + "-" * (col * 2 + 2))
    for i in range(max(len(expected), len(actual))):
        exp_str = _fmt_task(expected[i]) if i < len(expected) else "<missing>"
        act_str = _fmt_task(actual[i])   if i < len(actual)   else "<missing>"
        marker = "  " if exp_str == act_str else "≠ "
        print(f"{marker}{exp_str:<{col}}  {act_str}")


def print_plan_score(s: PlanScore) -> None:
    """Print one PlanScore immediately (call after each task completes)."""
    print(f"\n  {s.task_id}  score={s.score:.2f}  [{s.model}]")
    _print_plan_diff(s.expected_todo_list, s.generated_todo_list)


def print_report(report: BenchmarkReport) -> None:
    if report.plan_scores:
        print("\n=== Plan Quality Summary ===")
        for model, scores in _group_by_model(report.plan_scores).items():
            avg = mean(s.score for s in scores)
            print(f"  {model}: avg={avg:.2f}  (n={len(scores)})")

    if report.outcome_scores:
        print("\n=== Outcome Success Rate ===")
        for model, scores in _group_by_model(report.outcome_scores).items():
            rate = mean(s.success for s in scores)
            print(f"  {model}: {rate:.0%}  (n={len(scores)})")
            for s in scores:
                status = "PASS" if s.success else ("HITL" if s.halt_flag else "FAIL")
                print(f"    {s.task_id}: {status} — {s.reason}")

    if report.executor_results:
        print("\n=== Executor Instruction Following ===")
        for model, results in _group_by_model(report.executor_results).items():
            avg_combined = mean(r.combined_score for r in results)
            avg_seq = mean(r.sequence.score for r in results)
            outcome_rate = mean(r.outcome_success for r in results)
            print(f"  {model}: combined={avg_combined:.2f}  outcome={outcome_rate:.0%}  seq={avg_seq:.2f}  (n={len(results)})")
            for r in results:
                outcome_tag = "PASS" if r.outcome_success else ("HITL" if r.halt_flag else "FAIL")
                recovered_tag = " +recovered" if r.recovered else ""
                print(
                    f"    {r.case_id}: {outcome_tag}{recovered_tag} "
                    f"seq={r.sequence.score:.2f} combined={r.combined_score:.2f}"
                )
                print(f"      outcome: {r.verify_reason}")
                for mismatch in r.sequence.mismatches:
                    print(f"      seq: {mismatch}")
                for line in r.execution_log:
                    print(f"      log: {line}")


def save_report(report: BenchmarkReport, path: str | Path) -> None:
    path = Path(path)
    data = {
        "plan_scores": [
            {"task_id": s.task_id, "model": s.model, "score": s.score}
            for s in report.plan_scores
        ],
        "outcome_scores": [
            {"task_id": s.task_id, "model": s.model, "success": s.success,
             "reason": s.reason, "halt_flag": s.halt_flag}
            for s in report.outcome_scores
        ],
        "executor_results": [
            {
                "case_id": r.case_id, "model": r.model,
                "outcome_success": r.outcome_success, "halt_flag": r.halt_flag,
                "recovered": r.recovered, "verify_reason": r.verify_reason,
                "sequence_score": r.sequence.score,
                "sequence_mismatches": r.sequence.mismatches,
                "combined_score": r.combined_score,
            }
            for r in report.executor_results
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved to {path}")
