from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from SkiLib.genesis.types import GenesisSceneBundle, SceneObject, SceneTarget, TargetPose

RES_DIR = Path(__file__).resolve().parents[2] / "res"

TABLE_H = 0.72
TABLE_CX = 0.7
ROBOT_X = 0.35
TRAY_X = 0.35
TRAY_Y = 0.32
TRAY_H = 0.04
WALL_T = 0.01
TRAY_POS = (0.80, 0.22, TABLE_H)
ASSY_POS = (0.80, -0.22, TABLE_H)
TCP_LINK_NAME = "wrist_3_link"
# Vertical distance from wrist_3_link (TCP) to Robotiq 2F-85 finger contact surface.
# Tune this value if the gripper visually over- or under-shoots the part during pick.
TCP_OFFSET_Z = 0.172

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


def _add_tray(scene, gs, cx, cy, cz, w, d, h, t, color) -> None:
    scene.add_entity(
        gs.morphs.Box(size=(w, d, t), fixed=True, pos=(cx, cy, cz + t / 2)),
        surface=gs.surfaces.Default(color=color),
    )
    scene.add_entity(
        gs.morphs.Box(size=(t, d, h), fixed=True, pos=(cx - w / 2 + t / 2, cy, cz + h / 2)),
        surface=gs.surfaces.Default(color=color),
    )
    scene.add_entity(
        gs.morphs.Box(size=(t, d, h), fixed=True, pos=(cx + w / 2 - t / 2, cy, cz + h / 2)),
        surface=gs.surfaces.Default(color=color),
    )
    scene.add_entity(
        gs.morphs.Box(size=(w, t, h), fixed=True, pos=(cx, cy - d / 2 + t / 2, cz + h / 2)),
        surface=gs.surfaces.Default(color=color),
    )
    scene.add_entity(
        gs.morphs.Box(size=(w, t, h), fixed=True, pos=(cx, cy + d / 2 - t / 2, cz + h / 2)),
        surface=gs.surfaces.Default(color=color),
    )


def _make_target(name: str, pos: tuple[float, float, float], kind: str) -> SceneTarget:
    # Fixed top-down orientation for Phase 1. Motion primitives can refine this later.
    pose = TargetPose(
        name=name,
        pos=pos,
        quat=(0.0, 1.0, 0.0, 0.0),
        kind=kind,  # type: ignore[arg-type]
    )
    return SceneTarget(name=name, pose=pose)


def _build_targets() -> dict[str, SceneTarget]:
    tx, ty = TRAY_POS[0], TRAY_POS[1]
    ax, ay = ASSY_POS[0], ASSY_POS[1]

    # Finger-contact heights = centre of each part geometry (world Z).
    # Part A — Cylinder(height=0.06): centre at +0.030 above part_base
    # Part B — Box(z=0.050):          centre at +0.025 above part_base
    # Part C — Box(z=0.040):          centre at +0.020 above part_base
    _part_base_z = TABLE_H + WALL_T + 0.001  # bottom face of resting parts
    finger_z = {
        "A": _part_base_z + 0.030,
        "B": _part_base_z + 0.025,
        "C": _part_base_z + 0.020,
    }
    # TCP (wrist_3_link) must be TCP_OFFSET_Z above the desired finger-contact height.
    def _tcp(fz: float) -> float:
        return fz + TCP_OFFSET_Z

    approach_clearance = 0.14  # wrist lifts this far above pick TCP before transiting

    pick_positions = {
        "PartA": (tx - 0.06, ty + 0.05, _tcp(finger_z["A"])),
        "PartB": (tx + 0.06, ty + 0.05, _tcp(finger_z["B"])),
        "PartC": (tx, ty - 0.05, _tcp(finger_z["C"])),
    }
    place_positions = {
        "AssemblySlot_1": (ax - 0.06, ay + 0.05, _tcp(finger_z["A"])),
        "AssemblySlot_2": (ax + 0.06, ay + 0.05, _tcp(finger_z["B"])),
        "AssemblySlot_3": (ax, ay - 0.05, _tcp(finger_z["C"])),
    }

    targets: dict[str, SceneTarget] = {
        "Home_position": _make_target("Home_position", (0.55, 0.0, TABLE_H + 0.45), "home"),
    }

    for prefix, pos in pick_positions.items():
        targets[f"{prefix}_Pick"] = _make_target(f"{prefix}_Pick", pos, "pick")
        targets[f"{prefix}_Approach"] = _make_target(
            f"{prefix}_Approach",
            (pos[0], pos[1], pos[2] + approach_clearance),
            "approach",
        )

    for name, pos in place_positions.items():
        targets[name] = _make_target(name, pos, "place")
        targets[f"{name}_Approach"] = _make_target(
            f"{name}_Approach",
            (pos[0], pos[1], pos[2] + approach_clearance),
            "approach",
        )

    return targets


def build_genesis_scene(show_viewer: bool = False) -> GenesisSceneBundle:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/roboski-matplotlib")
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/roboski-cache")
    # pyrender's jit_render uses address_to_ptr() which is a JIT-only intrinsic;
    # NUMBA_DISABLE_JIT=1 crashes the viewer.  When show_viewer=True, force JIT on
    # regardless of what the env already has (must be set before numba is imported).
    # Headless/CI runs default to JIT-off for faster startup.
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
            camera_pos=(0.0, -1.8, 1.8),
            camera_lookat=(0.8, 0.0, 0.9),
        ),
    )
    if not show_viewer and os.getenv("ROBOSKI_GENESIS_BUILD_VISUALIZER", "0") not in {"1", "true", "True"}:
        scene._visualizer.build = lambda: None

    scene.add_entity(gs.morphs.Plane())
    scene.add_entity(
        gs.morphs.Box(size=(1.0, 0.8, TABLE_H), fixed=True, pos=(TABLE_CX, 0.0, TABLE_H / 2)),
        surface=gs.surfaces.Default(color=(0.85, 0.75, 0.6, 1.0)),
    )

    robot = scene.add_entity(
        gs.morphs.URDF(
            file=str(RES_DIR / "ur16e_robotiq.urdf"),
            fixed=True,
            pos=(ROBOT_X, 0.0, TABLE_H),
        ),
    )

    _add_tray(scene, gs, *TRAY_POS, TRAY_X, TRAY_Y, TRAY_H, WALL_T, color=(0.7, 0.7, 0.75, 1.0))
    _add_tray(scene, gs, *ASSY_POS, TRAY_X, TRAY_Y, TRAY_H, WALL_T, color=(0.4, 0.5, 0.6, 1.0))

    part_z = TABLE_H + WALL_T + 0.001
    tx, ty = TRAY_POS[0], TRAY_POS[1]
    part_a = scene.add_entity(
        gs.morphs.Cylinder(radius=0.025, height=0.06, pos=(tx - 0.06, ty + 0.05, part_z + 0.030)),
        surface=gs.surfaces.Default(color=(0.9, 0.2, 0.2, 1.0)),
    )
    part_b = scene.add_entity(
        gs.morphs.Box(size=(0.050, 0.040, 0.050), pos=(tx + 0.06, ty + 0.05, part_z + 0.025)),
        surface=gs.surfaces.Default(color=(0.2, 0.8, 0.3, 1.0)),
    )
    part_c = scene.add_entity(
        gs.morphs.Box(size=(0.060, 0.060, 0.040), pos=(tx, ty - 0.05, part_z + 0.020)),
        surface=gs.surfaces.Default(color=(0.2, 0.4, 0.9, 1.0)),
    )

    scene.build()

    arm_home = np.array([0, -np.pi / 2, np.pi / 2, -np.pi / 2, -np.pi / 2, 0], dtype=float)
    grip_home = np.zeros(max(0, int(robot.n_dofs) - 6), dtype=float)
    home_qpos = np.concatenate([arm_home, grip_home])
    arm_dofs = np.arange(6)
    gripper_dofs = np.arange(6, int(robot.n_dofs))

    robot.set_dofs_kp(np.array([4000] * 6 + [200] * max(0, int(robot.n_dofs) - 6)))
    robot.set_dofs_kv(np.array([100] * 6 + [10] * max(0, int(robot.n_dofs) - 6)))
    robot.set_dofs_force_range(
        np.array([-330] * 6 + [-50] * max(0, int(robot.n_dofs) - 6)),
        np.array([330] * 6 + [50] * max(0, int(robot.n_dofs) - 6)),
    )
    robot.set_dofs_position(home_qpos[: int(robot.n_dofs)])
    # Register the home configuration as the reset target.  scene.build() captures
    # _init_state before set_dofs_position() runs, so we overwrite it here.
    scene.reset(scene.get_state())

    return GenesisSceneBundle(
        scene=scene,
        robot=robot,
        objects={
            "Part_A_1": SceneObject("Part_A_1", part_a),
            "Part_B_1": SceneObject("Part_B_1", part_b),
            "Part_C_1": SceneObject("Part_C_1", part_c),
        },
        targets=_build_targets(),
        tools={"Robotiq_2F_85": robot},
        arm_dofs=arm_dofs,
        gripper_dofs=gripper_dofs,
        home_qpos=home_qpos[: int(robot.n_dofs)],
        tcp_link_name=TCP_LINK_NAME,
    )
