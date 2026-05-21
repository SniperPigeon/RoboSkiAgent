from __future__ import annotations

import os
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from SkiLib.genesis.config import (
    FMB_GRASP_Z_FRACTION_FROM_BOTTOM,
    FMB_MIN_GRASP_MARGIN_FROM_TOP,
    GRIPPER_DRIVER_JOINT_NAME,
    GRIPPER_DRIVER_JOINT_SAMPLES,
    GRIPPER_PAD_CLEARANCE,
    GRIPPER_PAD_LINK_NAMES,
    ROBOT_URDF,
    TCP_LINK_NAME,
    TCP_OFFSET_Z_FALLBACK,
    TCP_OFFSET_Z_OVERRIDE,
)
from SkiLib.genesis.assembly_specs import (
    default_grasp_profile_symbol,
    expected_object_yaw_for,
    tcp_yaw_for_object_yaw,
)
from SkiLib.genesis.types import GenesisSceneBundle, SceneObject, SceneTarget, TargetPose
from SkiLib.scenes.fmb.scene_config import load_fmb_scene_spec

RES_DIR = Path(__file__).resolve().parents[2] / "res"
FMB_DIR = RES_DIR / "fmb" / "processed"


def robot_urdf_path() -> Path:
    path = Path(ROBOT_URDF).expanduser()
    return path if path.is_absolute() else RES_DIR / path

# ── FMB scene constants loaded from SkiLib/scenes/fmb/scene.yaml ─────────────
FMB_SCENE_SPEC = load_fmb_scene_spec()

TABLE_H = FMB_SCENE_SPEC.table_height_m
TABLE_CX = FMB_SCENE_SPEC.table_center_x_m
TABLE_SIZE_X = FMB_SCENE_SPEC.table_size_x_m
TABLE_SIZE_Y = FMB_SCENE_SPEC.table_size_y_m
ROBOT_X = FMB_SCENE_SPEC.robot_x_m

FMB_BOARD_X = FMB_SCENE_SPEC.board_x_m
FMB_BOARD_Y = FMB_SCENE_SPEC.board_y_m
FMB_STAGE_Y = FMB_SCENE_SPEC.stage_y_m
FMB_PARTS = tuple(FMB_SCENE_SPEC.parts.keys())
FMB_PART_STAGE_X = {
    name: part.stage_x_m for name, part in FMB_SCENE_SPEC.parts.items()
}
FMB_PART_MESH = {
    name: part.mesh for name, part in FMB_SCENE_SPEC.parts.items()
}
FMB_PART_COLOR = {
    name: part.color for name, part in FMB_SCENE_SPEC.parts.items()
}
FMB_PART_HEIGHT = {
    name: part.height_m for name, part in FMB_SCENE_SPEC.parts.items()
}
FMB_PART_REF_Z_FROM_BOTTOM = {
    name: part.ref_z_from_bottom_m for name, part in FMB_SCENE_SPEC.parts.items()
}
FMB_PLACE_XY = {
    name: part.place_xy_m for name, part in FMB_SCENE_SPEC.parts.items()
}
FMB_PLACE_BOTTOM_Z = {
    name: part.place_bottom_z_m for name, part in FMB_SCENE_SPEC.parts.items()
}

def fmb_grasp_z_from_bottom(name: str, height: float | None = None) -> float:
    """Return the preferred finger-contact height from a part's bottom."""
    part_height = FMB_PART_HEIGHT.get(name, height if height is not None else GEAR_THICKNESS)
    grasp_z = part_height * FMB_GRASP_Z_FRACTION_FROM_BOTTOM
    max_grasp_z = max(0.0, part_height - FMB_MIN_GRASP_MARGIN_FROM_TOP)
    return float(min(grasp_z, max_grasp_z))


# Backward-compatible maps. New code should read assembly_specs.py, where
# expected_object_yaw_deg is the final object yaw and grasp profiles define TCP
# yaw offsets symbolically.
FMB_PLACE_YAW_DEG = {name: expected_object_yaw_for(name) for name in FMB_PARTS}
FMB_PICK_YAW_DEG = {
    name: tcp_yaw_for_object_yaw(name, 0.0, default_grasp_profile_symbol(name))
    for name in FMB_PARTS
}

# Backward-compatible name used by runtime.compute_pick_pose().
GEAR_THICKNESS = FMB_SCENE_SPEC.gear_thickness_m

APPROACH_CLEARANCE = FMB_SCENE_SPEC.approach_clearance_m

_GENESIS_INITIALIZED = False
_URDF_TOOL_OFFSET_CACHE: float | None = None


def _rpy_matrix(rpy: tuple[float, float, float]) -> np.ndarray:
    r, p, y = rpy
    cr, sr = np.cos(r), np.sin(r)
    cp, sp = np.cos(p), np.sin(p)
    cy, sy = np.cos(y), np.sin(y)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=float)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=float)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=float)
    return rz @ ry @ rx


def _axis_angle_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    norm = float(np.linalg.norm(axis))
    if norm == 0.0:
        return np.eye(3)
    x, y, z = axis / norm
    c, s = np.cos(angle), np.sin(angle)
    one_c = 1.0 - c
    return np.array([
        [c + x * x * one_c, x * y * one_c - z * s, x * z * one_c + y * s],
        [y * x * one_c + z * s, c + y * y * one_c, y * z * one_c - x * s],
        [z * x * one_c - y * s, z * y * one_c + x * s, c + z * z * one_c],
    ], dtype=float)


def _joint_motion_transform(axis: np.ndarray, q: float) -> np.ndarray:
    tf = np.eye(4)
    tf[:3, :3] = _axis_angle_matrix(axis, q)
    return tf


def _origin_transform(element) -> np.ndarray:
    origin = element.find("origin")
    xyz = (0.0, 0.0, 0.0)
    rpy = (0.0, 0.0, 0.0)
    if origin is not None:
        if origin.get("xyz"):
            xyz = tuple(float(v) for v in origin.get("xyz").split())  # type: ignore[assignment]
        if origin.get("rpy"):
            rpy = tuple(float(v) for v in origin.get("rpy").split())  # type: ignore[assignment]
    tf = np.eye(4)
    tf[:3, :3] = _rpy_matrix(rpy)
    tf[:3, 3] = np.asarray(xyz, dtype=float)
    return tf


def _quat_matrix_wxyz(quat: tuple[float, float, float, float]) -> np.ndarray:
    w, x, y, z = quat
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def _urdf_tool_offset_z() -> float | None:
    """Estimate TCP link to lowest configured finger-pad vertical offset."""
    global _URDF_TOOL_OFFSET_CACHE
    if _URDF_TOOL_OFFSET_CACHE is not None:
        return _URDF_TOOL_OFFSET_CACHE

    urdf = robot_urdf_path()
    try:
        root = ET.parse(urdf).getroot()
        joints = {}
        for joint in root.findall("joint"):
            parent = joint.find("parent")
            child = joint.find("child")
            if parent is None or child is None:
                continue
            axis_el = joint.find("axis")
            limit_el = joint.find("limit")
            mimic_el = joint.find("mimic")
            axis = np.asarray(
                [float(v) for v in axis_el.get("xyz").split()],
                dtype=float,
            ) if axis_el is not None and axis_el.get("xyz") else np.array([1.0, 0.0, 0.0])
            limit = (
                float(limit_el.get("lower", "0")),
                float(limit_el.get("upper", "0")),
            ) if limit_el is not None else (0.0, 0.0)
            mimic = None
            if mimic_el is not None:
                mimic = (
                    mimic_el.get("joint"),
                    float(mimic_el.get("multiplier", "1")),
                    float(mimic_el.get("offset", "0")),
                )
            joints[child.get("link")] = {
                "name": joint.get("name"),
                "type": joint.get("type"),
                "parent": parent.get("link"),
                "origin": _origin_transform(joint),
                "axis": axis,
                "limit": limit,
                "mimic": mimic,
            }

        link_boxes: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
        for link in root.findall("link"):
            lname = link.get("name")
            if not lname:
                continue
            boxes = []
            for geom_parent in list(link.findall("visual")) + list(link.findall("collision")):
                geometry = geom_parent.find("geometry")
                box = geometry.find("box") if geometry is not None else None
                if box is None or not box.get("size"):
                    continue
                size = np.asarray([float(v) for v in box.get("size").split()], dtype=float)
                boxes.append((_origin_transform(geom_parent), size))
            if boxes:
                link_boxes[lname] = boxes

        tcp_to_world_rot = _quat_matrix_wxyz(top_down_quat(0.0))
        lowest_world_z = 0.0
        found = False

        def _joint_q(joint_info: dict, finger_q: float) -> float:
            jtype = joint_info["type"]
            if jtype not in {"revolute", "continuous", "prismatic"}:
                return 0.0
            if joint_info["name"] == GRIPPER_DRIVER_JOINT_NAME:
                return finger_q
            mimic = joint_info["mimic"]
            if mimic and mimic[0] == GRIPPER_DRIVER_JOINT_NAME:
                return mimic[1] * finger_q + mimic[2]
            lower, upper = joint_info["limit"]
            return min(max(0.0, lower), upper)

        for link_name in GRIPPER_PAD_LINK_NAMES:
            chain = []
            cursor = link_name
            while cursor != TCP_LINK_NAME:
                joint_info = joints[cursor]
                parent = joint_info["parent"]
                chain.append(joint_info)
                cursor = parent

            for finger_q in GRIPPER_DRIVER_JOINT_SAMPLES:
                tf_tcp_link = np.eye(4)
                for joint_info in reversed(chain):
                    q = _joint_q(joint_info, float(finger_q))
                    motion_tf = _joint_motion_transform(joint_info["axis"], q)
                    tf_tcp_link = tf_tcp_link @ joint_info["origin"] @ motion_tf

                for geom_tf, size in link_boxes.get(link_name, []):
                    half = size / 2.0
                    for sx in (-half[0], half[0]):
                        for sy in (-half[1], half[1]):
                            for sz in (-half[2], half[2]):
                                local = np.array([sx, sy, sz, 1.0])
                                point_tcp = (tf_tcp_link @ geom_tf @ local)[:3]
                                point_world = tcp_to_world_rot @ point_tcp
                                lowest_world_z = min(lowest_world_z, float(point_world[2]))
                                found = True

        if not found:
            return None
        _URDF_TOOL_OFFSET_CACHE = abs(lowest_world_z) + GRIPPER_PAD_CLEARANCE
        return _URDF_TOOL_OFFSET_CACHE
    except Exception:
        return None


def tcp_offset_z() -> float:
    """Vertical wrist_3_link target offset above desired lower-pad contact height."""
    if TCP_OFFSET_Z_OVERRIDE:
        return float(TCP_OFFSET_Z_OVERRIDE)
    return _urdf_tool_offset_z() or TCP_OFFSET_Z_FALLBACK


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


TCP_OFFSET_Z = tcp_offset_z()


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
        tcp_yaw_deg=float(yaw_deg),
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
        grasp_z = fmb_grasp_z_from_bottom(name, height)
        sx, sy = FMB_PART_STAGE_X[name], FMB_STAGE_Y
        pick_tcp_z = TABLE_H + grasp_z + TCP_OFFSET_Z
        pick_pos = (sx, sy, pick_tcp_z)
        pick_appr = (sx, sy, pick_tcp_z + APPROACH_CLEARANCE)
        pick_yaw = tcp_yaw_for_object_yaw(name, 0.0, default_grasp_profile_symbol(name))

        px, py = FMB_PLACE_XY[name]
        place_tcp_z = FMB_PLACE_BOTTOM_Z[name] + grasp_z + TCP_OFFSET_Z
        place_pos = (px, py, place_tcp_z)
        place_appr = (px, py, place_tcp_z + APPROACH_CLEARANCE)
        expected_object_yaw = expected_object_yaw_for(name)
        place_yaw = tcp_yaw_for_object_yaw(
            name,
            expected_object_yaw,
            default_grasp_profile_symbol(name),
        )

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
        gs.morphs.Box(size=(TABLE_SIZE_X, TABLE_SIZE_Y, TABLE_H), fixed=True, pos=(TABLE_CX, 0.0, TABLE_H / 2)),
        surface=gs.surfaces.Default(color=(0.85, 0.75, 0.6, 1.0)),
    )

    # ── Robot ─────────────────────────────────────────────────────────────────
    robot = scene.add_entity(
        gs.morphs.URDF(
            file=str(robot_urdf_path()),
            fixed=True,
            pos=(ROBOT_X, 0.0, TABLE_H),
        ),
    )

    # ── FMB teaser board (fixed assembly platform) ────────────────────────────
    scene.add_entity(
        gs.morphs.Mesh(
            file=str(FMB_DIR / FMB_SCENE_SPEC.board_mesh),
            fixed=True,
            pos=(FMB_BOARD_X, FMB_BOARD_Y, TABLE_H),
            decimate=False,
            convexify=False,
        ),
        surface=gs.surfaces.Default(color=FMB_SCENE_SPEC.board_color),
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

    part_a_1 = _add_part("Part_A_1", FMB_PART_MESH["Part_A_1"], FMB_PART_COLOR["Part_A_1"])
    part_a_2 = _add_part("Part_A_2", FMB_PART_MESH["Part_A_2"], FMB_PART_COLOR["Part_A_2"])
    part_b = _add_part("Part_B", FMB_PART_MESH["Part_B"], FMB_PART_COLOR["Part_B"])
    part_c = _add_part("Part_C", FMB_PART_MESH["Part_C"], FMB_PART_COLOR["Part_C"])

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
