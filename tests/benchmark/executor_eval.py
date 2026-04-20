"""
executor_eval.py — Executor node-level integration test.

Directly calls executor_v2() with a pre-built GlobalState (no supervisor/planner/graph).
Primitives execute for real against a live RoboDK session.
LLM recovery is allowed to run without intervention.

Two dimensions evaluated:
  outcome_success  — TaskVerifier confirms physical result
  sequence_score   — generated primitive plan matches skill.md spec (Phase 1 quality)
                     penalized by RECOVERY_PENALTY if Phase 3 (recovery) was triggered

Requires: RoboDK running with the test scene loaded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel

from Agent.nodes.executor_v2 import executor_v2
from SkiLib.RDK_Test import reset_station
from SkiLib.verifiers import TaskVerifier, VerificationConfig
from tests.benchmark.validators import SequenceScore, score_sequence


@dataclass
class ExecutorEvalCase:
    case_id: str
    task_params: dict
    verification: VerificationConfig
    skill: str = "PickAndPlace"


@dataclass
class ExecutorEvalResult:
    case_id: str
    model: str
    outcome_success: bool
    halt_flag: bool
    recovered: bool
    verify_reason: str
    sequence: SequenceScore
    execution_log: list[str] = field(default_factory=list)

    @property
    def combined_score(self) -> float:
        """outcome (0.6 weight) + sequence (0.4 weight)."""
        return round(0.6 * int(self.outcome_success) + 0.4 * self.sequence.score, 3)


def run_executor_eval(
    llm: BaseChatModel,
    case: ExecutorEvalCase,
) -> ExecutorEvalResult:
    """
    Build a minimal GlobalState, call executor_v2 directly, then verify outcome
    and score the generated primitive sequence.

    RobotContext + SkillMdLoader must be initialized before calling.
    """
    from SkiLib.robotcontext import RobotContext
    ctx = RobotContext.instance()
    assert ctx is not None, (
        "RobotContext not initialized. "
        "Call RobotContext() and SkillMdLoader.instance() before running executor eval."
    )
    reset_station()

    state = {
        "current_task": {
            "task_id": case.case_id,
            "type": "auto",
            "skill": case.skill,
            "params": case.task_params,
        },
        "halt_flag": False,
        "halt_reason": None,
        "execution_log": [],
        "messages": [],
        "last_result": None,
        "todo_list": [],
        "robot_state": {},
        "plan_review_action": None,
        "intervention_action": None,
        "hitl_command": None,
    }
    
    

    result_state: dict = executor_v2(state, llm=llm)

    planned_steps: list[dict] = result_state.get("planned_steps", [])
    recovered: bool = result_state.get("recovered", False)

    seq_score = score_sequence(planned_steps, case.task_params, recovered)
    verify_result = TaskVerifier().verify(case.verification)

    model_name = getattr(llm, "model", getattr(llm, "model_name", str(type(llm).__name__)))
    return ExecutorEvalResult(
        case_id=case.case_id,
        model=model_name,
        outcome_success=verify_result.success,
        halt_flag=result_state.get("halt_flag", False),
        recovered=recovered,
        verify_reason=verify_result.reason,
        sequence=seq_score,
        execution_log=result_state.get("execution_log", []),
    )
