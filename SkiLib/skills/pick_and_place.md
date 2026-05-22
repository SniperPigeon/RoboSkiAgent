---
name: PickAndPlace
description: "Pick an object from a source target and place it at a destination target."
category: manipulation
version: "1.0"

parameters:
  item:
    type: str
    required: true
    description: "Genesis scene name of the workpiece to grasp and release."
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
    desription: "Target name for the precise placement point (TCP descends here via MoveL)."
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
  grasp_profile:
    type: str
    required: false
    default: "default"
    enum: ["default", "short_edge", "long_edge"]
    description: "Symbolic grasp profile from scenes. Default uses the per-part configured profile; never pass numeric yaw."

required_primitives: [MoveJ, MoveL, Grasp, Release]
---

# PickAndPlace — Execution Guide

## Purpose

Move a workpiece from a pick location to a place location using a structured
approach → grasp → transit → place → retract sequence.

> **Constraint**: All target parameters are Genesis symbolic names.
> **NEVER pass coordinates, matrix values, joint angles, or numeric yaw values
> as target names.**  Use `list_targets()` to verify available names if needed.

The optional `grasp_profile` is a symbolic clamp mode from
`SkiLib/scenes/fmb/assembly.md`. It is informational on the nominal path because
the static target names already encode the default TCP yaw. During dynamic
re-pick recovery, pass it to `compute_pick_pose()` so the fresh targets use the
same clamp convention.

## Pre-conditions

**After a HITL resume**, always call `get_attachment_state()` before continuing.
Use the result to determine whether the item has already been grasped, then
restart from the appropriate step rather than replaying from the beginning.

## Standard Execution Sequence

Execute the following steps in order.  Each step calls one primitive with the
concrete parameter value resolved at planning time.

1.  `{initial_motion}(target=home_position)` — Move to safe home position.
2.  `MoveL(target=pick_approach)` — Linear approach above pick site.
3.  `MoveL(target=pick_target)` — Linear descent to precise grasp point.
4.  `Grasp(expected_item=item)` — Close gripper; robot must be at pick_target.
5.  `MoveL(target=pick_approach)` — Linear retract (robot carries workpiece).
6.  `{transit_motion}(target=place_approach)` — Transit to approach above place site.
7.  `MoveL(target=place_target)` — Linear descent to precise placement point.
8.  `Release(expected_item=item)` — Open gripper; robot must be at place_target.
9.  `MoveL(target=place_approach)` — Linear retract with empty gripper.
10. `MoveL(target=home_position)` — Return to home position.

### Placement Verification (between steps 8 and 9)

After `Release`, verify placement before retracting:

```
add_get_object_position_check(
    item_name=<item>,
    check_field="is_placed",
    check_expected=True,
    on_fail="llm_recovery",
)
```

If `is_placed == False`, apply the recovery procedure below before proceeding
to step 9.

## Recovery

### Escalate Immediately (do not retry)

Call `escalate_to_hitl` immediately for any of the following:

- Any step returns **COLLISION**
- **MoveL** returns **IK_FAILURE** — do NOT substitute MoveJ; linear motion is
  required for safe approach/retract near the workpiece
- `compute_pick_pose` returns `is_pickable=False` (workpiece tilted beyond tolerance)

### Retry Allowed

**Grasp returns GRIPPER_FAILURE** (step 4):
First lift away from the workpiece by executing `MoveL(target=pick_approach)`.
Then re-execute step 3 (`MoveL` to `pick_target`) to correct TCP alignment, and
retry `Grasp`.  Maximum 2 retries.  Escalate if still failing.

**Release returns GRIPPER_FAILURE** (step 8):
Retry `Release` once in place without re-executing `MoveL`.  Escalate if still
failing.

**Placement check fails (`is_placed == False`)**:
First call `get_object_position(item_name=<item>)` and read `description` and
`xy_distance_to_nearest_place_m` to diagnose.  Then:

- *Gripper still holds the item* (`get_attachment_state` shows grasped):
  Re-execute step 7 (`MoveL` to `place_target`), retry `Release`, then re-check.
  Maximum 1 retry.  Escalate if still failing.

- *Item released but displaced*: the workpiece must be re-picked.  Do **not**
  reuse the static `pick_approach` / `pick_target` names — the object may no
  longer be at its original staging position.  Instead:
  1. Call `compute_pick_pose(item_name=<item>, grasp_profile=<grasp_profile>)`
     to read the current position and register fresh temporary targets.
  2. If `is_pickable=False` (workpiece tilted), escalate immediately.
  3. Use `approach_target_name` and `pick_target_name` from the result with
     `MoveL` and `Grasp`, then continue from step 6 (transit to `place_approach`).
  Maximum 1 full retry.  Escalate if still failing.

## Technical Details

`get_object_position` returns `is_placed=True` only when **all three** conditions
hold (thresholds defined in `SkiLib/genesis/config.py`):

- XY distance to nearest place target ≤ `PLACEMENT_XY_TOL_M` (default 5 mm;
  tight enough to distinguish 40 mm-spaced shaft slots)
- Z offset from expected resting height ≤ `PLACEMENT_Z_TOL_M` (default 5 mm;
  expected Z is derived from the place target TCP height minus the configured
  gripper offset and grasp height)
- Part tilt from horizontal ≤ `PLACEMENT_TILT_TOL_DEG` (default 8°; computed
  by rotating the part's local Z axis by the current world quaternion and
  taking the angle from world +Z)

`tilt_angle_deg` in the return dict is `None` if Genesis cannot expose
`get_quat()` on the entity; in that case the tilt check is skipped (fail open).
`description` always shows all three metrics, making recovery diagnosis
straightforward.
