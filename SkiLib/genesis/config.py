from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_float_samples(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    return tuple(float(item.strip()) for item in raw.split(",") if item.strip())


# Tunable parameters for Genesis runtime behaviour and robot/tool geometry.

# ── Placement verification (get_object_position) ─────────────────────────────

# XY tolerance for shaft-slot placement check.
# Must be less than half the shaft slot spacing (40 mm) to prevent a gear
# from being declared placed at the wrong slot.
PLACEMENT_XY_TOL_M: float = 0.005   # 5 mm

# Vertical tolerance after release.
# Detects tilted or fallen gears whose Z deviates from the expected resting height.
PLACEMENT_Z_TOL_M: float = 0.005    # ±5 mm

# Tilt tolerance: max angle (degrees) between the part's local +Z axis and
# world +Z. A part tilted > 8° is not seated reliably.
PLACEMENT_TILT_TOL_DEG: float = 8.0

# Yaw tolerance around world Z for orientation-sensitive assembly parts.
PLACEMENT_YAW_TOL_DEG: float = 10.0

# ── Robot / gripper geometry --------------------------------------------------

# Robot URDF path. Relative paths are resolved from the repository res/
# directory by scene.py; absolute paths are used as-is.
ROBOT_URDF: str = os.getenv("ROBOSKI_ROBOT_URDF", "ur16e_robotiq.urdf")

# Link controlled by IK and used as the nominal TCP frame in Genesis.
TCP_LINK_NAME: str = os.getenv("ROBOSKI_TCP_LINK_NAME", "wrist_3_link")

# Optional explicit TCP offset override. If unset, scene.py estimates the
# top-down vertical offset dynamically from the URDF finger pad geometry.
TCP_OFFSET_Z_OVERRIDE: str | None = os.getenv("ROBOSKI_TCP_OFFSET_Z")

# Fallback when URDF geometry cannot be parsed.
TCP_OFFSET_Z_FALLBACK: float = _env_float("ROBOSKI_TCP_OFFSET_Z_FALLBACK", 0.172)

# Extra vertical clearance above the lowest sampled finger-pad point.
GRIPPER_PAD_CLEARANCE: float = _env_float("ROBOSKI_GRIPPER_PAD_CLEARANCE", 0.005)

# Links whose box geometries approximate the lower gripping pads. For another
# gripper, set this to the relevant pad/contact link names.
GRIPPER_PAD_LINK_NAMES: tuple[str, ...] = _env_list(
    "ROBOSKI_GRIPPER_PAD_LINK_NAMES",
    ("left_inner_finger_pad", "right_inner_finger_pad"),
)

# Driver joint and sampled positions used to conservatively estimate the lowest
# pad point over the gripper opening/closing range.
GRIPPER_DRIVER_JOINT_NAME: str = os.getenv("ROBOSKI_GRIPPER_DRIVER_JOINT_NAME", "finger_joint")
GRIPPER_DRIVER_JOINT_SAMPLES: tuple[float, ...] = _env_float_samples(
    "ROBOSKI_GRIPPER_DRIVER_JOINT_SAMPLES",
    (0.0, 0.175, 0.35, 0.525, 0.7),
)

# ── FMB grasp geometry --------------------------------------------------------

# Finger lower-pad contact height measured from each part's bottom.
FMB_GRASP_Z_FRACTION_FROM_BOTTOM: float = _env_float("ROBOSKI_FMB_GRASP_Z_FRACTION", 0.75)
FMB_MIN_GRASP_MARGIN_FROM_TOP: float = _env_float("ROBOSKI_FMB_MIN_GRASP_MARGIN_FROM_TOP", 0.010)
