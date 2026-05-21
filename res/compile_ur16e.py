"""Compile UR16e xacro to URDF without ROS by mocking ament_index_python."""
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
UR_DESC = _REPO / "res/robot_models/Universal_Robots_ROS2_Description"
OUT = _REPO / "res/ur16e_raw.urdf"

# Mock ament_index_python so $(find ur_description) resolves to our local path
def _get_package_share_directory(pkg: str) -> str:
    if pkg == "ur_description":
        return str(UR_DESC)
    raise RuntimeError(f"Unknown ROS package: {pkg}")

ament_pkg = types.ModuleType("ament_index_python")
ament_pkg_sub = types.ModuleType("ament_index_python.packages")
ament_pkg_sub.get_package_share_directory = _get_package_share_directory
ament_pkg.packages = ament_pkg_sub
sys.modules["ament_index_python"] = ament_pkg
sys.modules["ament_index_python.packages"] = ament_pkg_sub

import xacro  # import after mock is in place

config = UR_DESC / "config/ur16e"
top = UR_DESC / "urdf/ur.urdf.xacro"

mappings = {
    "name": "ur16e",
    "ur_type": "ur16e",
    "joint_limit_params": str(config / "joint_limits.yaml"),
    "kinematics_params": str(config / "default_kinematics.yaml"),
    "physical_params": str(config / "physical_parameters.yaml"),
    "visual_params": str(config / "visual_parameters.yaml"),
    "force_abs_paths": "true",
}

print("Processing xacro ...")
doc = xacro.process_file(str(top), mappings=mappings)
xml_str = doc.toprettyxml(indent="  ")
OUT.write_text(xml_str)
print(f"OK — {len(xml_str):,} chars written to {OUT}")
