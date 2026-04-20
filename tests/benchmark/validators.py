"""
validators.py — Score executor_v2's generated primitive sequence against skill.md spec.

Sequence score is computed for Phase 1 (plan generation) only.
Recovery phase deviations are captured separately via the `recovered` flag.

Scoring approach
----------------
Each step in the generated plan is compared positionally against the expected
sequence. A step earns full credit if both the primitive name and the target
parameter resolve to the correct task param. Partial credit is given when only
the primitive name matches (wrong param) or the primitive is a valid motion
substitute (MoveL↔MoveJ where skill.md allows flexibility).

Final score = matched_steps / expected_steps
If the plan is longer than expected (extra steps), no extra penalty beyond
the mismatch in those positions — the ratio cap already handles it.

Recovery penalty: if executor_v2 had to enter Phase 3 (recovery), multiply
the sequence score by a configurable factor (default 0.8).
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Expected sequence for PickAndPlace (from skill.md)
# ---------------------------------------------------------------------------
# Each entry: (primitive_name, task_param_key, allow_substitute)
# allow_substitute: set of primitive names that skill.md explicitly allows in place
#   of this step (e.g. initial_motion can be MoveJ or MoveL).

@dataclass
class ExpectedStep:
    primitive: str
    task_param_key: str          # key in task_params whose value should be the target/item arg
    arg_key: str = "target"      # argument name passed to the primitive
    allow_substitutes: set[str] = field(default_factory=set)


EXPECTED_PICK_AND_PLACE: list[ExpectedStep] = [
    ExpectedStep("MoveJ",   "home_position",  allow_substitutes={"MoveL"}),   # initial_motion
    ExpectedStep("MoveL",   "pick_approach"),
    ExpectedStep("MoveL",   "pick_target"),
    ExpectedStep("Grasp",   "item",           arg_key="expected_item"),
    ExpectedStep("MoveL",   "pick_approach"),
    ExpectedStep("MoveJ",   "place_approach", allow_substitutes={"MoveL"}),   # transit_motion
    ExpectedStep("MoveL",   "place_target"),
    ExpectedStep("Release", "item",           arg_key="expected_item"),
    ExpectedStep("MoveL",   "place_approach"),
    ExpectedStep("MoveL",   "home_position"),
]

RECOVERY_PENALTY = 0.8   # multiply sequence score when recovery phase was triggered


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class SequenceScore:
    score: float                  # 0.0–1.0
    matched: int
    expected_total: int
    actual_total: int
    mismatches: list[str]         # human-readable description of each mismatch


def score_sequence(
    steps: list[dict],
    task_params: dict,
    recovered: bool,
    expected: list[ExpectedStep] = EXPECTED_PICK_AND_PLACE,
) -> SequenceScore:
    """
    Compare generated action steps against the expected sequence.

    Args:
        steps:       Raw step dicts from executor_v2's planned_steps field.
                     Only steps with type="action" are considered.
        task_params: The PickAndPlace params dict (item, home_position, …).
        recovered:   True if executor_v2 Phase 3 (recovery) ran.
        expected:    Expected sequence spec (defaults to PickAndPlace).
    """
    action_steps = [s for s in steps if s.get("type") == "action"]
    mismatches: list[str] = []
    matched = 0

    for i, exp in enumerate(expected):
        if i >= len(action_steps):
            mismatches.append(f"step {i+1}: missing — expected {exp.primitive}({exp.arg_key}={task_params.get(exp.task_param_key)})")
            continue

        actual = action_steps[i]
        actual_prim = actual.get("primitive", "")
        actual_args = actual.get("args", {})
        expected_value = task_params.get(exp.task_param_key, "")
        actual_value = actual_args.get(exp.arg_key, "")

        prim_ok = (actual_prim == exp.primitive) or (actual_prim in exp.allow_substitutes)
        param_ok = actual_value == expected_value

        if prim_ok and param_ok:
            matched += 1
        elif prim_ok:
            matched += 0.5
            mismatches.append(
                f"step {i+1}: {actual_prim} primitive OK but wrong param "
                f"({exp.arg_key}='{actual_value}' expected '{expected_value}')"
            )
        else:
            mismatches.append(
                f"step {i+1}: expected {exp.primitive} got {actual_prim} "
                f"(param: '{actual_value}' expected '{expected_value}')"
            )

    # Extra steps beyond expected length
    if len(action_steps) > len(expected):
        extra = len(action_steps) - len(expected)
        mismatches.append(f"{extra} extra step(s) beyond expected sequence")

    raw_score = matched / len(expected) if expected else 1.0
    final_score = raw_score * RECOVERY_PENALTY if recovered else raw_score

    return SequenceScore(
        score=round(min(1.0, final_score), 3),
        matched=int(matched),
        expected_total=len(expected),
        actual_total=len(action_steps),
        mismatches=mismatches,
    )
