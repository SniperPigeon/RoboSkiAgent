"""
Genesis assembly scene: UR16e + Robotiq 2F-85 + primitive workpiece objects.
Assembly context: pick parts from parts tray, place into assembly tray.

Named targets (symbolic, for RobotContext integration):
  PartA_Pick, PartB_Pick, PartC_Pick   — pick locations above each part
  AssemblySlot_1/2/3                   — place locations in assembly tray

Run with: conda run -n rsagent python res/genesis_scene_test.py
"""
from pathlib import Path
import numpy as np
import genesis as gs

import platform, os
if platform.system() == "Darwin":
    # Force Cocoa app activation so pyglet window survives
    os.environ.setdefault("PYOBJUS_MACOS_APPKIT_THREAD_CHECK", "0")
    
RES = Path(__file__).parent

gs.init(backend=gs.cpu, logging_level="warning")

scene = gs.Scene(
    show_viewer=True,
    viewer_options=gs.options.ViewerOptions(
        res=(1280, 720),
        camera_pos=(0.0, -1.8, 1.8),
        camera_lookat=(0.8, 0.0, 0.9),
    ),
)

# ── Ground ───────────────────────────────────────────────────────────────────
scene.add_entity(gs.morphs.Plane())

# ── Work table ───────────────────────────────────────────────────────────────
TABLE_H  = 0.72
TABLE_CX = 0.7    # centre x; table spans x = 0.2 ~ 1.2
scene.add_entity(
    gs.morphs.Box(size=(1.0, 0.8, TABLE_H), fixed=True, pos=(TABLE_CX, 0.0, TABLE_H / 2)),
    surface=gs.surfaces.Default(color=(0.85, 0.75, 0.6, 1.0)),
)

# ── Robot (UR16e + Robotiq 2F-85) — mounted on table top ─────────────────────
# Robot sits at the left-centre of the table; trays are in front / to the right
ROBOT_X = 0.35   # near the left edge of the table (x=0.2 + 0.15 margin)
robot = scene.add_entity(
    gs.morphs.URDF(
        file=str(RES / "ur16e_robotiq.urdf"),
        fixed=True,
        pos=(ROBOT_X, 0.0, TABLE_H),
    ),
)

# ── Parts tray (left side of table) ──────────────────────────────────────────
# Thin-walled tray: outer box minus interior. Approximated as 4 wall strips + base.
TRAY_X, TRAY_Y = 0.35, 0.32    # tray footprint
TRAY_H = 0.04                   # wall height
WALL_T = 0.01
TRAY_POS = (0.5, 0.22, TABLE_H)  # tray origin (bottom face)

def add_tray(scene, cx, cy, cz, w, d, h, t, color):
    """Add a simple open-top tray as 5 boxes (base + 4 walls)."""
    base_z = cz + t / 2
    scene.add_entity(gs.morphs.Box(size=(w, d, t),       fixed=True, pos=(cx, cy, base_z)),       surface=gs.surfaces.Default(color=color))
    scene.add_entity(gs.morphs.Box(size=(t, d, h),       fixed=True, pos=(cx - w/2 + t/2, cy, cz + h/2)), surface=gs.surfaces.Default(color=color))
    scene.add_entity(gs.morphs.Box(size=(t, d, h),       fixed=True, pos=(cx + w/2 - t/2, cy, cz + h/2)), surface=gs.surfaces.Default(color=color))
    scene.add_entity(gs.morphs.Box(size=(w, t, h),       fixed=True, pos=(cx, cy - d/2 + t/2, cz + h/2)), surface=gs.surfaces.Default(color=color))
    scene.add_entity(gs.morphs.Box(size=(w, t, h),       fixed=True, pos=(cx, cy + d/2 - t/2, cz + h/2)), surface=gs.surfaces.Default(color=color))

# Parts tray — in front of robot (right side of table), assembly tray further right
TRAY_POS  = (0.80,  0.22, TABLE_H)
ASSY_POS  = (0.80, -0.22, TABLE_H)
add_tray(scene, *TRAY_POS, TRAY_X, TRAY_Y, TRAY_H, WALL_T, color=(0.7, 0.7, 0.75, 1.0))

# Assembly tray (right side) — darker
add_tray(scene, *ASSY_POS, TRAY_X, TRAY_Y, TRAY_H, WALL_T, color=(0.4, 0.5, 0.6, 1.0))

# ── Parts (A=red cylinder, B=green box, C=blue box) ──────────────────────────
PART_Z = TABLE_H + WALL_T + 0.001   # sitting on tray base
TX, TY = TRAY_POS[0], TRAY_POS[1]  # tray centre

# Part A — cylinder Ø50mm × 60mm
part_a = scene.add_entity(
    gs.morphs.Cylinder(radius=0.025, height=0.06, pos=(TX - 0.06, TY + 0.05, PART_Z + 0.030)),
    surface=gs.surfaces.Default(color=(0.9, 0.2, 0.2, 1.0)),
)

# Part B — box 50×40×50mm
part_b = scene.add_entity(
    gs.morphs.Box(size=(0.050, 0.040, 0.050), pos=(TX + 0.06, TY + 0.05, PART_Z + 0.025)),
    surface=gs.surfaces.Default(color=(0.2, 0.8, 0.3, 1.0)),
)

# Part C — box 60×60×40mm
part_c = scene.add_entity(
    gs.morphs.Box(size=(0.060, 0.060, 0.040), pos=(TX, TY - 0.05, PART_Z + 0.020)),
    surface=gs.surfaces.Default(color=(0.2, 0.4, 0.9, 1.0)),
)

# ── Named target registry (symbolic → world xyz) ─────────────────────────────
# These will feed into RobotContext when integrating with Agent/
AX, AY = ASSY_POS[0], ASSY_POS[1]
NAMED_TARGETS = {
    "PartA_Pick":     (TX - 0.06, TY + 0.05, TABLE_H + WALL_T + 0.06 + 0.05),
    "PartB_Pick":     (TX + 0.06, TY + 0.05, TABLE_H + WALL_T + 0.05 + 0.05),
    "PartC_Pick":     (TX,        TY - 0.05, TABLE_H + WALL_T + 0.04 + 0.05),
    "AssemblySlot_1": (AX - 0.06, AY + 0.05, TABLE_H + WALL_T + 0.06 + 0.05),
    "AssemblySlot_2": (AX + 0.06, AY + 0.05, TABLE_H + WALL_T + 0.05 + 0.05),
    "AssemblySlot_3": (AX,        AY - 0.05, TABLE_H + WALL_T + 0.04 + 0.05),
}

# ── Build ─────────────────────────────────────────────────────────────────────
scene.build()

print(f"Robot DOFs : {robot.n_dofs}")
print(f"Joints     : {[j.name for j in robot.joints]}")
print(f"Named targets: {list(NAMED_TARGETS.keys())}")

# ── PD control — hold home position ──────────────────────────────────────────
arm_home  = np.array([0, -np.pi/2, np.pi/2, -np.pi/2, -np.pi/2, 0], dtype=float)
grip_home = np.zeros(max(0, int(robot.n_dofs) - 6))
home_qpos = np.concatenate([arm_home, grip_home])

robot.set_dofs_kp(np.array([4000]*6 + [200]*6))
robot.set_dofs_kv(np.array([100]*6  + [10]*6))
robot.set_dofs_force_range(
    np.array([-330]*6 + [-50]*6),
    np.array([ 330]*6 + [ 50]*6),
)
robot.set_dofs_position(home_qpos)

print("\nHolding home position. Ctrl+C to exit.")
try:
    while True:
        robot.control_dofs_position(home_qpos)
        scene.step()
except KeyboardInterrupt:
    print("Stopped.")
