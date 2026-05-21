"""
Diagnostic script: print actual Genesis scene positions vs. expected target positions.

Run after activating the conda environment:
    python tests/diag_scene_positions.py

Prints a table comparing:
  - Gear staging positions (get_pos()) vs. pick target XY
  - Shaft/place target XY vs. scene.py SHAFT_X values
  - Expected Z for placed gears vs. get_object_position() expected_z formula
"""

import os
import sys

os.environ.setdefault("ROBOSKI_GENESIS_VIEWER", "0")
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from SkiLib.genesis.scene import (
    GEAR_BASE_X, GEAR_BASE_Y, GEAR_BASE_H,
    GEAR_THICKNESS, SHAFT_X, GEAR_STAGE_X, STAGE_Y,
    TABLE_H, TCP_OFFSET_Z, APPROACH_CLEARANCE,
    build_genesis_scene,
)

print("Building Genesis scene (no viewer)...")
bundle = build_genesis_scene(show_viewer=False)
print("Scene built.\n")

place_tcp_z = TABLE_H + GEAR_BASE_H + GEAR_THICKNESS / 2 + TCP_OFFSET_Z
pick_tcp_z  = TABLE_H + GEAR_THICKNESS / 2 + TCP_OFFSET_Z

print("=" * 65)
print("TARGET REGISTRY (from scene.py constants)")
print("=" * 65)
for name in sorted(bundle.targets):
    t = bundle.targets[name]
    p = t.pose.pos
    print(f"  {name:<35} ({p[0]:.4f}, {p[1]:.4f}, {p[2]:.4f})  kind={t.pose.kind}")

print()
print("=" * 65)
print("GEAR OBJECT INITIAL POSITIONS (entity.get_pos())")
print("=" * 65)

for obj_name, obj in bundle.objects.items():
    raw = obj.entity.get_pos()
    pos = raw.tolist() if hasattr(raw, "tolist") else list(raw)
    size = obj_name.split("_")[1]  # Small / Medium / Large
    expected_x = GEAR_STAGE_X[size]
    expected_y = STAGE_Y
    expected_z_coa = TABLE_H + GEAR_THICKNESS / 2  # CoM-based expected Z
    expected_z_origin = TABLE_H + GEAR_THICKNESS    # origin-based expected Z
    print(f"  {obj_name}:")
    print(f"    get_pos()    = ({pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f})")
    print(f"    expected XY  = ({expected_x:.4f}, {expected_y:.4f})")
    print(f"    expected Z   = {expected_z_coa:.4f} (CoM) | {expected_z_origin:.4f} (STL origin)")
    dx = abs(pos[0] - expected_x) * 1000
    dy = abs(pos[1] - expected_y) * 1000
    print(f"    XY error     = ({dx:.1f} mm, {dy:.1f} mm)")

print()
print("=" * 65)
print("SHAFT ALIGNMENT CHECK (place target vs. expected shaft centre)")
print("=" * 65)

for size in ("Small", "Medium", "Large"):
    shaft_x = SHAFT_X[size]
    t_name  = f"ShaftSlot_{size}_Place"
    t = bundle.targets[t_name]
    tp = t.pose.pos
    print(f"  {size}:")
    print(f"    Place target  = ({tp[0]:.5f}, {tp[1]:.5f}, {tp[2]:.5f})")
    print(f"    Expected X    = GEAR_BASE_X {shaft_x - GEAR_BASE_X:+.5f} = {shaft_x:.5f}")
    print(f"    Expected Y    = {GEAR_BASE_Y:.5f}")
    print(f"    X diff (code) = {(tp[0] - shaft_x)*1000:.2f} mm")

print()
print("=" * 65)
print("Z FORMULA CHECK")
print("=" * 65)
print(f"  pick_tcp_z  = TABLE_H + GEAR_THICKNESS/2 + TCP_OFFSET_Z")
print(f"              = {TABLE_H} + {GEAR_THICKNESS/2} + {TCP_OFFSET_Z} = {pick_tcp_z:.4f} m")
print(f"  pick_obj_z  = {TABLE_H + GEAR_THICKNESS/2:.4f} m  (gear CoM in staging)")
print(f"  place_tcp_z = TABLE_H + GEAR_BASE_H + GEAR_THICKNESS/2 + TCP_OFFSET_Z")
print(f"              = {TABLE_H} + {GEAR_BASE_H} + {GEAR_THICKNESS/2} + {TCP_OFFSET_Z} = {place_tcp_z:.4f} m")
print(f"  placed_obj_z (expected) = place_tcp_z - TCP_OFFSET_Z = {place_tcp_z - TCP_OFFSET_Z:.4f} m")
print(f"  placed_obj_z (formula)  = TABLE_H + GEAR_BASE_H + GEAR_THICKNESS/2 = {TABLE_H + GEAR_BASE_H + GEAR_THICKNESS/2:.4f} m")
