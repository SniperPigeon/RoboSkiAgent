from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ItemExpectation:
    """Expected final state for one workpiece."""
    item_name: str
    near_target: str | None = None      # RoboDK Target/Frame name the item should be near
    tolerance_mm: float = 10.0
    detached_from_gripper: bool = True  # item should not be held by gripper


@dataclass
class VerificationConfig:
    """Input spec for TaskVerifier — one entry per workpiece involved in the task."""
    task_instruction: str
    expected_items: list[ItemExpectation] = field(default_factory=list)


@dataclass
class VerificationResult:
    """Output of TaskVerifier — used as the SFT label for a trajectory."""
    success: bool
    reason: str
    method: Literal["rule", "llm"] = "rule"
    evidence: dict = field(default_factory=dict)
