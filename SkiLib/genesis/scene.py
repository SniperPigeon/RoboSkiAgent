from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from SkiLib.genesis.types import GenesisSceneBundle, SceneObject, SceneTarget, TargetPose

RES_DIR = Path(__file__).resolve().parents[2] / "res"
FMB_DIR = RES_DIR / "fmb" / "processed"

# ── Table / robot constants ───────────────────────────────────────────────────
TABLE_H   = 0.72
TABLE_CX  = 0.7
ROBOT_X   = 0.35
TCP_LINK_NAME = "wrist_3_link"
# Vertical distance from wrist_3_link (TCP) to Robotiq 2F-140 finger contact surface.
# Tune this value if the gripper visually over- or under-shoots the part during pick.
TCP_OFFSET_Z = 0.172

# ── FMB scene constants ───────────────────────────────────────────────────────
FMB_BOARD_X = 0.78
FMB_BOARD_Y = 0.00
FMB_STAGE_Y = 0.28
FMB_PART_STAGE_X = {
    "Part_A_1": 0.54,
    "Part_A_2": 0.68,
    "Part_B": 0.82,
    "Part_C": 0.98,
}

# Meshes were normalized to metres with bottom-centre origins in res/fmb/processed.
FMB_PART_HEIGHT = {
    "Part_A_1": 0.100,
    "Part_A_2": 0.100,
    "Part_B": 0.037,
    "Part_C": 0.100,
}
FMB_PART_REF_Z_FROM_BOTTOM = {
    "Part_A_1": 0.044138,
    "Part_A_2": 0.044138,
    "Part_B": 0.018500,
    "Part_C": 0.039964,
}
FMB_PARTS = ("Part_A_1", "Part_A_2", "Part_B", "Part_C")

# Approximate assembly locations from the original Board 1 STEP body positions,
# expressed relative to teasor_board's centre.  teasor_board is the central tray; the
# remaining four bodies are staged as pickable parts.
FMB_PLACE_XY = {
    "Part_A_1": (FMB_BOARD_X - 0.068, FMB_BOARD_Y),
    "Part_A_2": (FMB_BOARD_X + 0.068, FMB_BOARD_Y),
    "Part_B": (FMB_BOARD_X, FMB_BOARD_Y),
    "Part_C": (FMB_BOARD_X, FMB_BOARD_Y),
}
FMB_PLACE_BOTTOM_Z = {
    "Part_A_1": TABLE_H + 0.005,
    "Part_A_2": TABLE_H + 0.005,
    "Part_B": TABLE_H + 0.040,
    "Part_C": TABLE_H + 0.005,
}
FMB_PLACE_YAW_DEG = {
    "Part_A_1": 0.0,
    "Part_A_2": 0.0,
    "Part_B": 0.0,
    "Part_C": 0.0,
}
FMB_PICK_YAW_DEG = {
    "Part_A_1": 0.0,
    "Part_A_2": 0.0,
    "Part_B": 90.0,
    "Part_C": 0.0,
}

# Backward-compatible name used by runtime.compute_pick_pose().
GEAR_THICKNESS = 0.050

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


def top_down_quat(yaw_deg: float = 0.0) -> tuple[float, float, float, float]:
    """Create a top-down TCP quaternion with an additional world-Z yaw."""
    half = np.radians(yaw_deg) / 2.0
    # world yaw qz=(cos,0,0,sin) composed with top-down q=(0,1,0,0)
    return (0.0, float(np.cos(half)), float(np.sin(half)), 0.0)


def make_target(
    name: str,
    pos: tuple[float, float, float],
    kind: str,
    *,
    yaw_deg: float = 0.0,
    expected_object_yaw_deg: float | None = None,
    quat: tuple[float, float, float, float] | None = None,
) -> SceneTarget:
    """Create a SceneTarget with an explicit TCP orientation."""
    pose = TargetPose(
        name=name,
        pos=pos,
        quat=quat if quat is not None else top_down_quat(yaw_deg),
        kind=kind,  # type: ignore[arg-type]
        yaw_deg=float(yaw_deg),
        expected_object_yaw_deg=expected_object_yaw_deg,
    )
    return SceneTarget(name=name, pose=pose)


def _build_fmb_targets() -> dict[str, SceneTarget]:
    """Build symbolic target registry for the FMB pick/place scene.

    All z-coordinates are TCP positions (wrist_3_link), not finger-contact heights.
    TCP_z = finger_contact_z + TCP_OFFSET_Z.
    """
    targets: dict[str, SceneTarget] = {
        "Home_position": make_target(
            "Home_position", (0.55, 0.0, TABLE_H + 0.45), "home"
        ),
    }

    for name in FMB_PARTS:
        height = FMB_PART_HEIGHT[name]
        sx, sy = FMB_PART_STAGE_X[name], FMB_STAGE_Y
        pick_tcp_z = TABLE_H + height / 2 + TCP_OFFSET_Z
        pick_pos = (sx, sy, pick_tcp_z)
        pick_appr = (sx, sy, pick_tcp_z + APPROACH_CLEARANCE)
        pick_yaw = FMB_PICK_YAW_DEG[name]

        px, py = FMB_PLACE_XY[name]
        place_tcp_z = FMB_PLACE_BOTTOM_Z[name] + height / 2 + TCP_OFFSET_Z
        place_pos = (px, py, place_tcp_z)
        place_appr = (px, py, place_tcp_z + APPROACH_CLEARANCE)
        expected_object_yaw = FMB_PLACE_YAW_DEG[name]
        # A welded grasp preserves object-to-TCP yaw. To land the object at the
        # desired yaw, the TCP place yaw must include the pick yaw offset.
        place_yaw = pick_yaw + expected_object_yaw

        targets[f"{name}_Approach"] = make_target(
            f"{name}_Approach", pick_appr, "approach", yaw_deg=pick_yaw
        )
        targets[f"{name}_Pick"] = make_target(
            f"{name}_Pick", pick_pos, "pick", yaw_deg=pick_yaw
        )
        targets[f"{name}_Place_Approach"] = make_target(
            f"{name}_Place_Approach", place_appr, "approach", yaw_deg=place_yaw
        )
        targets[f"{name}_Place"] = make_target(
            f"{name}_Place",
            place_pos,
            "place",
            yaw_deg=place_yaw,
            expected_object_yaw_deg=expected_object_yaw,
        )

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
            camera_lookat=(FMB_BOARD_X, FMB_BOARD_Y, 0.85),
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

    # ── FMB teaser board (fixed assembly platform) ────────────────────────────
    scene.add_entity(
        gs.morphs.Mesh(
            file=str(FMB_DIR / "teasor_board.stl"),
            fixed=True,
            pos=(FMB_BOARD_X, FMB_BOARD_Y, TABLE_H),
            decimate=False,
            convexify=False,
        ),
        surface=gs.surfaces.Default(color=(0.30, 0.40, 0.60, 1.0)),
    )

    # ── FMB parts (dynamic, staged beside the board) ──────────────────────────
    def _add_part(name: str, filename: str, color: tuple):
        return scene.add_entity(
            gs.morphs.Mesh(
                file=str(FMB_DIR / filename),
                pos=(FMB_PART_STAGE_X[name], FMB_STAGE_Y, TABLE_H),
                decimate=False,
                convexify=True,
            ),
            surface=gs.surfaces.Default(color=color),
        )

    part_a_1 = _add_part("Part_A_1", "part_A_1.stl", (0.20, 0.70, 0.80, 1.0))
    part_a_2 = _add_part("Part_A_2", "part_A_2.stl", (0.65, 0.35, 0.75, 1.0))
    part_b = _add_part("Part_B", "part_B.stl", (0.90, 0.75, 0.20, 1.0))
    part_c = _add_part("Part_C", "part_C.stl", (0.85, 0.45, 0.20, 1.0))

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
            "Part_A_1": SceneObject("Part_A_1", part_a_1),
            "Part_A_2": SceneObject("Part_A_2", part_a_2),
            "Part_B": SceneObject("Part_B", part_b),
            "Part_C": SceneObject("Part_C", part_c),
        },
        targets=_build_fmb_targets(),
        tools={"Robotiq_2F_140": robot},
        arm_dofs=arm_dofs,
        gripper_dofs=gripper_dofs,
        home_qpos=home_qpos[: int(robot.n_dofs)],
        tcp_link_name=TCP_LINK_NAME,
    )
