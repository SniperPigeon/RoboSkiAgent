# Tunable parameters for Genesis runtime behaviour.
# Geometry constants that describe the scene (shaft spacing, TCP offset, etc.)
# live in scene.py; this file holds thresholds that may need tuning per
# workpiece or assembly task without touching scene geometry.

# ── Placement verification (get_object_position) ─────────────────────────────

# XY tolerance for shaft-slot placement check.
# Must be less than half the shaft slot spacing (40 mm) to prevent a gear
# from being declared placed at the wrong slot.
PLACEMENT_XY_TOL_M: float = 0.005   # 5 mm

# Vertical tolerance after release.
# Detects tilted or fallen gears whose Z deviates from the expected resting height.
PLACEMENT_Z_TOL_M: float = 0.005    # ±5 mm

# Tilt tolerance: max angle (degrees) between gear disc normal and world +Z.
# Gear disc normal is the local Y axis in STL coords; euler=(-90,0,0) at spawn
# rotates it to world +Z.  A gear tilted > 8° is not seated and cannot engage
# the shaft slot reliably.
PLACEMENT_TILT_TOL_DEG: float = 8.0
