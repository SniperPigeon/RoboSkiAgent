---
name: PickAndPlace
description: "Pick an object from a source target and place it at a destination target."
category: manipulation
version: "1.0"

parameters:
  item:
    type: str
    required: true
    description: "RoboDK name of the workpiece to grasp and release."
  home_position:
    type: str
    required: true
    description: "Target name for the safe start/end position (home)."
  pick_approach:
    type: str
    required: true
    description: "Target name for the approach/depart waypoint above the pick location."
  pick_target:
    type: str
    required: true
    description: "Target name for the precise grasp point (TCP descends here via MoveL)."
  place_approach:
    type: str
    required: true
    description: "Target name for the approach/depart waypoint above the place location."
  place_target:
    type: str
    required: true
    description: "Target name for the precise placement point (TCP descends here via MoveL)."
  transit_motion:
    type: str
    required: false
    default: "MoveL"
    enum: ["MoveJ", "MoveL"]
    description: "Motion primitive for the pick→place transit (pick_approach to place_approach). MoveL is safer for long-range moves."
  initial_motion:
    type: str
    required: false
    default: "MoveL"
    enum: ["MoveJ", "MoveL"]
    description: "Motion primitive for the initial move to home_position. MoveL is recommended."

required_primitives: [MoveJ, MoveL, Grasp, Release]
---

# PickAndPlace — Execution Guide

## Purpose
Move a workpiece from a pick location to a place location using a structured
approach → grasp → transit → place → retract sequence.

All target parameters are RoboDK symbolic names (strings visible in the RoboDK
station item tree). **NEVER pass coordinates or matrix values as target names.**

## Standard Execution Sequence

Execute the following steps in order.  Each step calls one primitive with the
concrete parameter value resolved at planning time.

1. `{initial_motion}(target=home_position)` — Move to safe home position.
2. `MoveL(target=pick_approach)` — Linear approach above pick site.
3. `MoveL(target=pick_target)` — Linear descent to precise grasp point.
4. `Grasp(expected_item=item)` — Close gripper; robot must be at pick_target.
5. `MoveL(target=pick_approach)` — Linear retract (robot carries workpiece).
6. `{transit_motion}(target=place_approach)` — Transit to approach above place site.
7. `MoveL(target=place_target)` — Linear descent to precise placement point.
8. `Release(expected_item=item)` — Open gripper; robot must be at place_target.
9. `MoveL(target=place_approach)` — Linear retract with empty gripper.
10. `MoveL(target=home_position)` — Return to home position.

## Recovery Hints

Apply these hints **before** calling `escalate_to_hitl`.

- **Grasp returns GRIPPER_FAILURE**:
  The TCP may not be precisely at pick_target.  Re-execute step 3 (`MoveL` to
  `pick_target`), then retry `Grasp`.  Maximum 2 retries.  If still failing after
  2 retries, call `escalate_to_hitl`.

- **MoveL returns IK_FAILURE**:
  Call `escalate_to_hitl` immediately.  Do NOT substitute MoveJ for a MoveL step —
  linear motion is required for safe approach/retract near the workpiece.

- **Release returns GRIPPER_FAILURE**:
  Retry `Release` once in place (without re-executing MoveL).  If still failing,
  call `escalate_to_hitl`.

- **Any step returns COLLISION**:
  Call `escalate_to_hitl` immediately.  Do not attempt alternative paths.

- **MoveJ returns COLLISION** (step 1 or 6):
  Call `escalate_to_hitl` immediately.

## Notes

- Before Grasp (step 4) or Release (step 8), call `get_gripper_state()` if you are
  uncertain about the current gripper status (e.g. after a HITL resume).
- After a HITL resume, always call `get_gripper_state()` to determine whether the
  item has already been grasped before deciding which step to restart from.
- The `item`, `home_position`, `pick_approach`, `pick_target`, `place_approach`,
  and `place_target` values are all exact symbol names from the RoboDK station.
  Use `list_targets()` to verify available target names if needed.
