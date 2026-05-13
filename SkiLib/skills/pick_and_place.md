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
  grasp_profile:
    type: str
    required: false
    default: "default"
    enum: ["default", "short_edge", "long_edge"]
    description: "Symbolic grasp profile from assembly.md. Default uses the per-part code-defined profile; never pass numeric yaw."

required_primitives: [MoveJ, MoveL, Grasp, Release]
---

# PickAndPlace — Execution Guide

## Purpose
Move a workpiece from a pick location to a place location using a structured
approach → grasp → transit → place → retract sequence.

All target parameters are Genesis symbolic names. **NEVER pass coordinates,
matrix values, joint angles, or numeric yaw values as target names.**

The optional `grasp_profile` is a symbolic clamp mode from
`SkiLib/genesis/assembly.md`. It is informational on the nominal path because
the static target names already encode the default TCP yaw. During dynamic
re-pick recovery, pass it to `compute_pick_pose()` so the fresh targets use the
same clamp convention.

## Standard Execution Sequence

Execute the following steps in order.  Each step calls one primitive or check
with the concrete parameter value resolved at planning time.

1.  `{initial_motion}(target=home_position)` — Move to safe home position.
2.  `MoveL(target=pick_approach)` — Linear approach above pick site.
3.  `MoveL(target=pick_target)` — Linear descent to precise grasp point.
4.  `Grasp(expected_item=item)` — Close gripper; robot must be at pick_target.
5.  `MoveL(target=pick_approach)` — Linear retract (robot carries workpiece).
6.  `{transit_motion}(target=place_approach)` — Transit to approach above place site.
7.  `MoveL(target=place_target)` — Linear descent to precise placement point.
8.  `Release(expected_item=item)` — Open gripper; robot must be at place_target.
8.5 `CHECK get_object_position(item_name=item) → is_placed == True` — Verify the
    workpiece landed within placement tolerance.  Register as:
    ```
    add_get_object_position_check(
        item_name=<item>,
        check_field="is_placed",
        check_expected=True,
        on_fail="llm_recovery",
    )
    ```
9.  `MoveL(target=place_approach)` — Linear retract with empty gripper.
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

- **Placement check fails (is_placed == False at step 8.5)**:
  The workpiece slipped or was not released at the correct position.  Call
  `get_object_position(item_name=<item>)` to read the current `description` and
  `xy_distance_to_nearest_place_m`.  Then:
  - If the gripper still holds the item (`get_attachment_state` shows it grasped):
    re-execute step 7 (`MoveL` to `place_target`), then retry `Release` and the
    check.  Maximum 1 retry.
  - If the item has been released but is displaced: the workpiece must be re-picked.
    The gear may no longer be at the original staging position, so do **not** reuse
    the static `pick_approach` / `pick_target` names.  Instead:
    1. Call `compute_pick_pose(item_name=<item>, grasp_profile=<grasp_profile>)`
       to get the current object position and register fresh temporary targets.
    2. Check `is_pickable` in the result.  If False (gear tilted), call
       `escalate_to_hitl` immediately.
    3. Use `approach_target_name` and `pick_target_name` from the result with MoveL
       and Grasp, then proceed from step 6 (transit to place_approach).
    Maximum 1 full retry.
  - If still failing after retries, call `escalate_to_hitl`.

- **Any step returns COLLISION**:
  Call `escalate_to_hitl` immediately.  Do not attempt alternative paths.

- **MoveJ returns COLLISION** (step 1 or 6):
  Call `escalate_to_hitl` immediately.

## Notes

- Before Grasp (step 4) or Release (step 8), call `get_attachment_state()` if you
  are uncertain about the current gripper status (e.g. after a HITL resume).
- After a HITL resume, always call `get_attachment_state()` to determine whether the
  item has already been grasped before deciding which step to restart from.
- The `item`, `home_position`, `pick_approach`, `pick_target`, `place_approach`,
  and `place_target` values are all exact symbol names from the Genesis scene.
  Use `list_targets()` to verify available target names if needed.
- `get_object_position` returns `is_placed=True` only when **all three** conditions hold
  (thresholds are in `SkiLib/genesis/config.py`):
  - XY distance to nearest place target ≤ `PLACEMENT_XY_TOL_M` (default 5 mm,
    tight enough to distinguish 40 mm-spaced shaft slots)
  - Z offset from expected resting height ≤ `PLACEMENT_Z_TOL_M` (default 5 mm;
    expected Z is derived from the place target TCP height minus the configured
    gripper offset and grasp height)
  - Part tilt from horizontal ≤ `PLACEMENT_TILT_TOL_DEG` (default 8°; computed
    by rotating the part's local Z axis by the current world quaternion and
    taking the angle from world +Z)
- `tilt_angle_deg` in the return dict is `None` if Genesis cannot expose `get_quat()`
  on the entity; in that case the tilt check is skipped (fail open).
- `description` always shows all three metrics, making recovery diagnosis straightforward.
