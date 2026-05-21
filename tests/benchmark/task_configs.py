"""
task_configs.py — Load benchmark tasks from plan_claude.jsonl.

VerificationConfig is derived automatically from each task's expected_todo_list:
for every auto PickAndPlace task, item → item_name, place_target → near_target.
Manual tasks are skipped (no physical verification possible).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from SkiLib.verifiers.base import ItemExpectation, VerificationConfig

_DATA_FILE = Path(__file__).resolve().parent.parent.parent / "trainer" / "apoptimizer" / "plan_claude.jsonl"
_DEFAULT_TOLERANCE_MM = 15.0


@dataclass
class TaskConfig:
    task_id: str
    plan_input: str
    expected_todo_list: list[dict]
    verification: VerificationConfig


def _derive_verification(plan_input: str, todo_list: list[dict]) -> VerificationConfig:
    """Build VerificationConfig from a todo_list by extracting PickAndPlace place targets."""
    items: list[ItemExpectation] = []
    seen: set[str] = set()
    for task in todo_list:
        if task.get("type") != "auto" or task.get("skill") != "PickAndPlace":
            continue
        params = task.get("params", {})
        item_name   = params.get("item")
        place_target = params.get("place_target")
        if item_name and place_target and item_name not in seen:
            items.append(ItemExpectation(
                item_name=item_name,
                near_target=place_target,
                tolerance_mm=_DEFAULT_TOLERANCE_MM,
            ))
            seen.add(item_name)
    return VerificationConfig(task_instruction=plan_input, expected_items=items)


def load_task_configs(task_ids: list[str] | None = None) -> list[TaskConfig]:
    """Load tasks from plan_claude.jsonl, optionally filtered by task_ids."""
    configs: list[TaskConfig] = []
    with open(_DATA_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            tid = rec["task_id"]
            if task_ids is not None and tid not in task_ids:
                continue
            todo_list = rec["expected"]
            configs.append(TaskConfig(
                task_id=tid,
                plan_input=rec["plan_input"],
                expected_todo_list=todo_list,
                verification=_derive_verification(rec["plan_input"], todo_list),
            ))
    return configs


def load_verifiable_tasks() -> list[TaskConfig]:
    """Tasks that have at least one ItemExpectation (i.e. contain a PickAndPlace step)."""
    return [t for t in load_task_configs() if t.verification.expected_items]
