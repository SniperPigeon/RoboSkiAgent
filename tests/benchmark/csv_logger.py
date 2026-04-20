"""
csv_logger.py — Incremental CSV writer for benchmark results.

Appends one row per result immediately after each task completes.
Multiple runs accumulate in the same file; group by (task_id, model, mode)
and average score across run_ids to get stable estimates.

CSV schema (plan scores):
    run_id, timestamp, mode, task_id, model, score

CSV schema (executor results):
    run_id, timestamp, mode, case_id, model, outcome_success, recovered,
    sequence_score, combined_score
"""
from __future__ import annotations

import csv
import uuid
from datetime import datetime
from pathlib import Path


class CsvLogger:
    """
    Open a CSV in append mode; write a header only when the file is new.

    Usage:
        logger = CsvLogger("results.csv")
        logger.log_plan(score)
        logger.log_executor(result)
        logger.close()

    Or use as context manager:
        with CsvLogger("results.csv") as log:
            log.log_plan(score)
    """

    _PLAN_FIELDS = ["run_id", "timestamp", "mode", "task_id", "model", "score"]
    _EXEC_FIELDS = [
        "run_id", "timestamp", "mode", "case_id", "model",
        "outcome_success", "recovered", "sequence_score", "combined_score",
    ]

    def __init__(self, path: str | Path, mode: str = "plan"):
        self.path = Path(path)
        self.mode = mode
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self._is_new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        fields = self._EXEC_FIELDS if mode == "executor" else self._PLAN_FIELDS
        self._writer = csv.DictWriter(self._fh, fieldnames=fields)
        if self._is_new:
            self._writer.writeheader()

    def log_plan(self, score) -> None:
        from tests.benchmark.plan_evaluator import PlanScore
        assert isinstance(score, PlanScore)
        self._writer.writerow({
            "run_id":    self.run_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode":      self.mode,
            "task_id":   score.task_id,
            "model":     score.model,
            "score":     round(score.score, 4),
        })
        self._fh.flush()

    def log_executor(self, result) -> None:
        from tests.benchmark.executor_eval import ExecutorEvalResult
        assert isinstance(result, ExecutorEvalResult)
        self._writer.writerow({
            "run_id":         self.run_id,
            "timestamp":      datetime.now().isoformat(timespec="seconds"),
            "mode":           self.mode,
            "case_id":        result.case_id,
            "model":          result.model,
            "outcome_success": int(result.outcome_success),
            "recovered":      int(result.recovered),
            "sequence_score": round(result.sequence.score, 4),
            "combined_score": round(result.combined_score, 4),
        })
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
