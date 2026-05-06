"""
Genesis gear assembly scene: IndustRealKit gear set on gear base.
Planning task: pick gears from staging area and place onto base shafts in order.

Ordering constraint (tests Planner):
  gear_base already fixed → Gear_Small → Gear_Medium → Gear_Large
  Each gear must be placed before the next can be loaded (spatial dependency).

Assets (IndustRealKit, meters, no scale needed):
  gear_base:   150×75×25 mm, z_min=0 (sits flush on table)
  gear_small:  ⌀22mm × 25mm thick, disc in XZ plane, z_min=-11mm
  gear_medium: ⌀42mm × 25mm thick, disc in XZ plane, z_min=-21mm
  gear_large:  ⌀62mm × 25mm thick, disc in XZ plane, z_min=-31mm

Run with: conda run -n rsagent python res/genesis_scene_test.py
"""
from pathlib import Path
import numpy as np
import genesis as gs
import platform, os

if platform.system() == "Darwin":
    os.environ.setdefault("PYOBJUS_MACOS_APPKIT_THREAD_CHECK", "0")

RES   = Path(__file__).parent
GEARS = RES / "industrealkit/gears/stl"

gs.init(logging_level="warning")

scene = gs.Scene(
    show_viewer=True,
    viewer_options=gs.options.ViewerOptions(
        res=(1280, 720),
        camera_pos=(0.5, -1.6, 1.6),
        camera_lookat=(0.78, 0.0, 0.85),
    ),
)

# ── Ground + table ────────────────────────────────────────────────────────────
TABLE_H = 0.72
scene.add_entity(gs.morphs.Plane())
scene.add_entity(
    gs.morphs.Box(size=(1.0, 0.8, TABLE_H), fixed=True, pos=(0.7, 0.0, TABLE_H / 2)),
    surface=gs.surfaces.Default(color=(0.85, 0.75, 0.6, 1.0)),
)

# ── Robot ─────────────────────────────────────────────────────────────────────
robot = scene.add_entity(
    gs.morphs.URDF(file=str(RES / "ur16e_robotiq.urdf"), fixed=True, pos=(0.35, 0.0, TABLE_H)),
)

# ── Gear base (fixed, assembly target platform) ───────────────────────────────
# 150×75×25mm plate, origin at centre of bottom face, z_min=0 → sits on table.
# Placed slightly right of robot reach centre; gears go on top of this.
BASE_X, BASE_Y = 0.78, 0.0
scene.add_entity(
    gs.morphs.Mesh(
        file=str(GEARS / "gear_base.stl"),
        fixed=True,
        pos=(BASE_X, BASE_Y, TABLE_H),
    ),
    surface=gs.surfaces.Default(color=(0.30, 0.40, 0.60, 1.0)),
)

# Shaft target positions on the base (evenly spaced along x, top of 25mm plate).
# These are approximate — tune after visual confirmation of shaft locations.
BASE_TOP_Z = TABLE_H + 0.025
SHAFT_XS   = [BASE_X - 0.040, BASE_X, BASE_X + 0.040]   # small, medium, large

# ── Staging area: gears waiting to be picked (y+ side, spread along x) ────────
# Gears have their disc in XZ plane; z_min = -radius, z_max = +radius.
# Place z = TABLE_H + radius so the bottom of the disc rests on the table.
STAGE_Y = 0.28   # staging row y-coordinate

# Rotate -90° around X so disc lies flat (XY plane) like a hockey puck.
# After rotation: original y-thickness [0, 25mm] maps to z [0, 25mm], z_min=0.
GEAR_THICKNESS = 0.025   # 25 mm, same for all three gears

# With euler=(-90,0,0): original y:[0,25mm] maps to z:[-25,0]mm → z_min=-25mm.
# Lift by GEAR_THICKNESS so the bottom face sits flush on the table.
def add_gear(path, color, stage_x):
    scene.add_entity(
        gs.morphs.Mesh(
            file=str(path),
            pos=(stage_x, STAGE_Y, TABLE_H + GEAR_THICKNESS),
            euler=(-90, 0, 0),
        ),
        surface=gs.surfaces.Default(color=color),
    )
    return stage_x, STAGE_Y

g_small_cx,  g_small_cy  = add_gear(GEARS/"gear_small.stl",  (0.90, 0.75, 0.20, 1.0), 0.62)
g_medium_cx, g_medium_cy = add_gear(GEARS/"gear_medium.stl", (0.85, 0.50, 0.15, 1.0), 0.78)
g_large_cx,  g_large_cy  = add_gear(GEARS/"gear_large.stl",  (0.75, 0.25, 0.15, 1.0), 0.94)

GEAR_TOP_Z = TABLE_H + GEAR_THICKNESS   # z=0 after lift = bottom face on table, top = TABLE_H+0.025

# ── Named target registry ─────────────────────────────────────────────────────
# Pick targets: TCP just above gear top-of-disc.
# Place targets: TCP just above each shaft slot on the base.
# Approach: 140mm above pick/place point (existing scene.py convention).
APPR = 0.14

def pick_targets(cx, cy, label):
    return {
        f"{label}_Approach": (cx, cy, GEAR_TOP_Z + APPR),
        f"{label}_Pick":     (cx, cy, GEAR_TOP_Z + 0.010),
    }

def place_targets(shaft_x, label):
    return {
        f"{label}_Approach": (shaft_x, BASE_Y, BASE_TOP_Z + APPR),
        f"{label}_Place":    (shaft_x, BASE_Y, BASE_TOP_Z + 0.010),
    }

NAMED_TARGETS = {}
NAMED_TARGETS.update(pick_targets(g_small_cx,  g_small_cy,  "GearSmall"))
NAMED_TARGETS.update(pick_targets(g_medium_cx, g_medium_cy, "GearMedium"))
NAMED_TARGETS.update(pick_targets(g_large_cx,  g_large_cy,  "GearLarge"))
NAMED_TARGETS.update(place_targets(SHAFT_XS[0], "ShaftSlot_Small"))
NAMED_TARGETS.update(place_targets(SHAFT_XS[1], "ShaftSlot_Medium"))
NAMED_TARGETS.update(place_targets(SHAFT_XS[2], "ShaftSlot_Large"))

# ── Build ─────────────────────────────────────────────────────────────────────
scene.build()

print(f"Robot DOFs : {robot.n_dofs}")
print(f"Named targets:")
for name, pos in NAMED_TARGETS.items():
    print(f"  {name}: {tuple(round(v, 4) for v in pos)}")

# ── PD control — hold home ────────────────────────────────────────────────────
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

print("\nHolding home. Ctrl+C to exit.")
try:
    while True:
        robot.control_dofs_position(home_qpos)
        scene.step()
except KeyboardInterrupt:
    print("Stopped.")
