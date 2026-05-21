"""
outcome_evaluator.py — Verify physical task outcome after a full graph run.
Wraps TaskVerifier and produces an OutcomeScore.
"""
from __future__ import annotations

from dataclasses import dataclass

from SkiLib.verifiers import TaskVerifier
from tests.benchmark.task_configs import TaskConfig


@dataclass
class OutcomeScore:
    task_id: str
    model: str
    success: bool
    reason: str
    halt_flag: bool
    execution_log: list[str]


def evaluate_outcome(
    model_name: str,
    task: TaskConfig,
    final_state: dict,
) -> OutcomeScore:
    """
    Verify that the physical scene matches task.verification after graph execution.

    Args:
        model_name:  Name of the LLM used (for reporting).
        task:        TaskConfig with a non-None verification field.
        final_state: The dict returned by graph.invoke().
    """
    assert task.verification is not None, (
        f"Task {task.task_id} has no VerificationConfig — cannot evaluate outcome."
    )

    result = TaskVerifier().verify(task.verification)
    return OutcomeScore(
        task_id=task.task_id,
        model=model_name,
        success=result.success,
        reason=result.reason,
        halt_flag=final_state.get("halt_flag", False),
        execution_log=final_state.get("execution_log", []),
    )
