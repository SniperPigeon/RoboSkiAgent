from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from SkiLib.genesis.types import GenesisSceneBundle, SceneObject, SceneTarget, TargetPose

RES_DIR = Path(__file__).resolve().parents[2] / "res"
GEAR_DIR = RES_DIR / "industrealkit" / "gears" / "stl"

# ── Table / robot constants ───────────────────────────────────────────────────
TABLE_H   = 0.72
TABLE_CX  = 0.7
ROBOT_X   = 0.35
TCP_LINK_NAME = "wrist_3_link"
# Vertical distance from wrist_3_link (TCP) to Robotiq 2F-85 finger contact surface.
# Tune this value if the gripper visually over- or under-shoots the part during pick.
TCP_OFFSET_Z = 0.172

# ── Gear scene constants ──────────────────────────────────────────────────────
# gear_base STL: 150×75×25 mm, z_min=0, origin at centre of bottom face.
GEAR_BASE_X = 0.78
GEAR_BASE_Y = 0.00
GEAR_BASE_H = 0.025   # plate height (z_max of STL)

# All three gears share the same 25 mm thickness (y-span in STL).
# euler=(-90,0,0) rotates disc to XY plane; original y:[0,25mm] → z:[-25,0]mm.
# Lift pos_z by GEAR_THICKNESS so the bottom face sits flush on the table.
GEAR_THICKNESS = 0.025

# Staging area: gears wait here before being picked (robot's y+ side).
STAGE_Y = 0.28
GEAR_STAGE_X = {"Small": 0.62, "Medium": 0.78, "Large": 0.94}

# Shaft x-positions on gear_base, measured from gear_base.stl Kasa circle-fit
# on top cross-section (z = 22–25 mm), STL in metres.  Shaft radius = 4.14 mm.
# Shafts are NOT uniformly spaced; old 40 mm assumption was 10–20 mm off.
#   Large  shaft: circle centre x = -30.25 mm → GEAR_BASE_X - 0.03025
#   Medium shaft: circle centre x = +20.25 mm → GEAR_BASE_X + 0.02025
#   Small  shaft: circle centre x = +50.75 mm → GEAR_BASE_X + 0.05075
# All three shafts sit at y = 0.00 mm (gear_base Y-symmetric, confirmed by fit).
# Measurement uncertainty ≈ ±0.5 mm (STL polygon facets → imperfect circles).
SHAFT_X = {
    "Large":  GEAR_BASE_X - 0.03025,
    "Medium": GEAR_BASE_X + 0.02025,
    "Small":  GEAR_BASE_X + 0.05075,
}

APPROACH_CLEARANCE = 0.14   # TCP lifts this far above pick/place before transiting

_GENESIS_INITIALIZED = False


def _ensure_genesis_initialized(gs) -> None:
    global _GENESIS_INITIALIZED
    if _GENESIS_INITIALIZED:
        return
    _patch_empty_cpu_name()
    backend_name = os.getenv("ROBOSKI_GENESIS_BACKEND", "cpu")
    backend = getattr(gs, backend_name)
    gs.init(backend=backend, logging_level="warning")
    _GENESIS_INITIALIZED = True


def _patch_empty_cpu_name() -> None:
    """Work around Genesis CPU init when py-cpuinfo returns no name fields."""
    try:
        import genesis.utils.misc as genesis_misc  # noqa: PLC0415
    except Exception:
        return

    original = genesis_misc.cpuinfo.get_cpu_info
    info = original()
    if any(info.get(key) for key in ("brand_raw", "hardware_raw", "vendor_id_raw")):
        return

    def _patched_get_cpu_info():
        patched = dict(original())
        patched.setdefault("brand_raw", "Unknown CPU")
        return patched

    genesis_misc.cpuinfo.get_cpu_info = _patched_get_cpu_info


def make_target(name: str, pos: tuple[float, float, float], kind: str) -> SceneTarget:
    """Create a SceneTarget with fixed top-down TCP orientation."""
    pose = TargetPose(
        name=name,
        pos=pos,
        quat=(0.0, 1.0, 0.0, 0.0),
        kind=kind,  # type: ignore[arg-type]
    )
    return SceneTarget(name=name, pose=pose)


def _build_gear_targets() -> dict[str, SceneTarget]:
    """Build symbolic target registry for the gear assembly scene.

    All z-coordinates are TCP positions (wrist_3_link), not finger-contact heights.
    TCP_z = finger_contact_z + TCP_OFFSET_Z.

    Pick: fingers grip gear at mid-thickness, gear lying flat on table.
      finger_z = TABLE_H + GEAR_THICKNESS / 2
      tcp_z    = finger_z + TCP_OFFSET_Z

    Place: gear rests on top of gear_base plate.
      finger_z = TABLE_H + GEAR_BASE_H + GEAR_THICKNESS / 2
      tcp_z    = finger_z + TCP_OFFSET_Z
    """
    pick_tcp_z  = TABLE_H + GEAR_THICKNESS / 2 + TCP_OFFSET_Z   # 0.7325 + 0.172 = 0.9045
    place_tcp_z = TABLE_H + GEAR_BASE_H + GEAR_THICKNESS / 2 + TCP_OFFSET_Z  # 0.7575 + 0.172 = 0.9295

    targets: dict[str, SceneTarget] = {
        "Home_position": make_target(
            "Home_position", (0.55, 0.0, TABLE_H + 0.45), "home"
        ),
    }

    for size in ("Small", "Medium", "Large"):
        sx, sy = GEAR_STAGE_X[size], STAGE_Y
        pick_pos   = (sx, sy, pick_tcp_z)
        pick_appr  = (sx, sy, pick_tcp_z + APPROACH_CLEARANCE)

        shaft_x = SHAFT_X[size]
        place_pos  = (shaft_x, GEAR_BASE_Y, place_tcp_z)
        place_appr = (shaft_x, GEAR_BASE_Y, place_tcp_z + APPROACH_CLEARANCE)

        label_pick  = f"Gear{size}"
        label_place = f"ShaftSlot_{size}"

        targets[f"{label_pick}_Approach"] = make_target(f"{label_pick}_Approach", pick_appr,  "approach")
        targets[f"{label_pick}_Pick"]     = make_target(f"{label_pick}_Pick",     pick_pos,   "pick")
        targets[f"{label_place}_Approach"]= make_target(f"{label_place}_Approach",place_appr, "approach")
        targets[f"{label_place}_Place"]   = make_target(f"{label_place}_Place",   place_pos,  "place")

    return targets


def build_genesis_scene(show_viewer: bool = False) -> GenesisSceneBundle:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/roboski-matplotlib")
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/roboski-cache")
    if show_viewer:
        os.environ["NUMBA_DISABLE_JIT"] = os.getenv("ROBOSKI_NUMBA_DISABLE_JIT", "0")
    else:
        os.environ.setdefault("NUMBA_DISABLE_JIT", os.getenv("ROBOSKI_NUMBA_DISABLE_JIT", "1"))

    import genesis as gs  # noqa: PLC0415

    _ensure_genesis_initialized(gs)

    scene = gs.Scene(
        show_viewer=show_viewer,
        viewer_options=gs.options.ViewerOptions(
            res=(1280, 720),
            camera_pos=(0.5, -1.6, 1.6),
            camera_lookat=(0.78, 0.0, 0.85),
        ),
    )
    if not show_viewer and os.getenv("ROBOSKI_GENESIS_BUILD_VISUALIZER", "0") not in {"1", "true", "True"}:
        scene._visualizer.build = lambda: None

    # ── Ground + table ────────────────────────────────────────────────────────
    scene.add_entity(gs.morphs.Plane())
    scene.add_entity(
        gs.morphs.Box(size=(1.0, 0.8, TABLE_H), fixed=True, pos=(TABLE_CX, 0.0, TABLE_H / 2)),
        surface=gs.surfaces.Default(color=(0.85, 0.75, 0.6, 1.0)),
    )

    # ── Robot ─────────────────────────────────────────────────────────────────
    robot = scene.add_entity(
        gs.morphs.URDF(
            file=str(RES_DIR / "ur16e_robotiq.urdf"),
            fixed=True,
            pos=(ROBOT_X, 0.0, TABLE_H),
        ),
    )

    # ── Gear base (fixed assembly platform) ───────────────────────────────────
    # STL z_min=0 → origin at bottom face centre, sits flush on table at TABLE_H.
    scene.add_entity(
        gs.morphs.Mesh(
            file=str(GEAR_DIR / "gear_base.stl"),
            fixed=True,
            pos=(GEAR_BASE_X, GEAR_BASE_Y, TABLE_H),
        ),
        surface=gs.surfaces.Default(color=(0.30, 0.40, 0.60, 1.0)),
    )

    # ── Gears (dynamic, in staging area) ─────────────────────────────────────
    # euler=(-90,0,0): disc lies flat (XY plane), teeth face up.
    # STL y:[0,25mm] → after rotation z:[-25,0]mm; lift pos_z by GEAR_THICKNESS.
    def _add_gear(filename: str, color: tuple, stage_x: float):
        return scene.add_entity(
            gs.morphs.Mesh(
                file=str(GEAR_DIR / filename),
                pos=(stage_x, STAGE_Y, TABLE_H + GEAR_THICKNESS),
                euler=(-90, 0, 0),
            ),
            surface=gs.surfaces.Default(color=color),
        )

    gear_small  = _add_gear("gear_small.stl",  (0.90, 0.75, 0.20, 1.0), GEAR_STAGE_X["Small"])
    gear_medium = _add_gear("gear_medium.stl", (0.85, 0.50, 0.15, 1.0), GEAR_STAGE_X["Medium"])
    gear_large  = _add_gear("gear_large.stl",  (0.75, 0.25, 0.15, 1.0), GEAR_STAGE_X["Large"])

    # ── Build + PD init ───────────────────────────────────────────────────────
    scene.build()

    arm_home   = np.array([0, -np.pi / 2, np.pi / 2, -np.pi / 2, -np.pi / 2, 0], dtype=float)
    grip_home  = np.zeros(max(0, int(robot.n_dofs) - 6), dtype=float)
    home_qpos  = np.concatenate([arm_home, grip_home])
    arm_dofs   = np.arange(6)
    gripper_dofs = np.arange(6, int(robot.n_dofs))

    robot.set_dofs_kp(np.array([4000] * 6 + [200] * max(0, int(robot.n_dofs) - 6)))
    robot.set_dofs_kv(np.array([100]  * 6 + [10]  * max(0, int(robot.n_dofs) - 6)))
    robot.set_dofs_force_range(
        np.array([-330] * 6 + [-50] * max(0, int(robot.n_dofs) - 6)),
        np.array([ 330] * 6 + [ 50] * max(0, int(robot.n_dofs) - 6)),
    )
    robot.set_dofs_position(home_qpos[: int(robot.n_dofs)])
    scene.reset(scene.get_state())

    return GenesisSceneBundle(
        scene=scene,
        robot=robot,
        objects={
            "Gear_Small_1":  SceneObject("Gear_Small_1",  gear_small),
            "Gear_Medium_1": SceneObject("Gear_Medium_1", gear_medium),
            "Gear_Large_1":  SceneObject("Gear_Large_1",  gear_large),
        },
        targets=_build_gear_targets(),
        tools={"Robotiq_2F_85": robot},
        arm_dofs=arm_dofs,
        gripper_dofs=gripper_dofs,
        home_qpos=home_qpos[: int(robot.n_dofs)],
        tcp_link_name=TCP_LINK_NAME,
    )
