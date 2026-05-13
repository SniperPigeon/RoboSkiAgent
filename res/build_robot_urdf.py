"""
Build combined UR16e + Robotiq 2F-140 URDF without ROS.

Steps:
1. Mock ament_index_python so xacro $(find pkg) resolves to local paths
2. Compile UR16e xacro -> URDF XML
3. Compile Robotiq 2F-140 xacro -> URDF XML
4. Merge: append all Robotiq links/joints into UR16e robot element
5. Add fixed joint: tool0 -> robotiq_arg2f_base_link
6. Replace package:// paths with absolute file paths
7. Write combined URDF
"""
import sys
import types
from pathlib import Path
from xml.dom import minidom
import xml.etree.ElementTree as ET

_REPO    = Path(__file__).resolve().parents[1]
UR_DESC  = _REPO / "temp/Universal_Robots_ROS2_Description"
ROBOTIQ  = _REPO / "temp/robotiq_2f_140_source/robotiq_2f_140_gripper_visualization"
OUT      = _REPO / "res/ur16e_robotiq.urdf"

# ── Mock ament_index_python ──────────────────────────────────────────────────
PKG_MAP = {
    "ur_description": str(UR_DESC),
    "robotiq_2f_140_gripper_visualization": str(ROBOTIQ),
}

def _get_pkg(pkg: str) -> str:
    if pkg in PKG_MAP:
        return PKG_MAP[pkg]
    raise RuntimeError(f"Unknown ROS package: {pkg!r}")

_ament = types.ModuleType("ament_index_python")
_ament_pkg = types.ModuleType("ament_index_python.packages")
_ament_pkg.get_package_share_directory = _get_pkg
_ament.packages = _ament_pkg
sys.modules["ament_index_python"] = _ament
sys.modules["ament_index_python.packages"] = _ament_pkg

import xacro  # must import after mock

# ── Compile UR16e ────────────────────────────────────────────────────────────
print("Compiling UR16e ...")
cfg = UR_DESC / "config/ur16e"
ur_doc = xacro.process_file(str(UR_DESC / "urdf/ur.urdf.xacro"), mappings={
    "name": "ur16e",
    "ur_type": "ur16e",
    "joint_limit_params": str(cfg / "joint_limits.yaml"),
    "kinematics_params":  str(cfg / "default_kinematics.yaml"),
    "physical_params":    str(cfg / "physical_parameters.yaml"),
    "visual_params":      str(cfg / "visual_parameters.yaml"),
    "force_abs_paths":    "true",
})
ur_xml = ur_doc.toxml()

# ── Compile Robotiq 2F-140 ───────────────────────────────────────────────────
print("Compiling Robotiq 2F-140 ...")
rq_doc = xacro.process_file(str(ROBOTIQ / "urdf/robotiq_arg2f_140_model.xacro"), mappings={})
rq_xml = rq_doc.toxml()

# ── Merge ────────────────────────────────────────────────────────────────────
print("Merging ...")
ur_root  = ET.fromstring(ur_xml)
rq_root  = ET.fromstring(rq_xml)

# Copy all link/joint elements from Robotiq into UR16e robot element
for child in rq_root:
    if child.tag in ("link", "joint", "transmission", "gazebo"):
        ur_root.append(child)

# Fixed joint: UR16e tool0 -> Robotiq base (offset 0 because tool0 is already at flange)
attach = ET.SubElement(ur_root, "joint", name="ur16e_to_robotiq", type="fixed")
ET.SubElement(attach, "parent", link="tool0")
ET.SubElement(attach, "child",  link="robotiq_arg2f_base_link")
ET.SubElement(attach, "origin", xyz="0 0 0", rpy="0 0 0")

# ── Fix package:// and file:/// paths ────────────────────────────────────────
combined_xml = ET.tostring(ur_root, encoding="unicode")
for pkg, path in PKG_MAP.items():
    combined_xml = combined_xml.replace(f"package://{pkg}", path)
# urdfpy resolves filenames relative to the URDF location, so file:/// must be stripped
combined_xml = combined_xml.replace("file:///", "/")

# Pretty-print
pretty = minidom.parseString(combined_xml).toprettyxml(indent="  ")
# Remove redundant xml declaration added by toprettyxml
lines = pretty.split("\n")
if lines[0].startswith("<?xml"):
    lines[0] = '<?xml version="1.0"?>'
OUT.write_text("\n".join(lines))

# ── Summary ──────────────────────────────────────────────────────────────────
root = ET.fromstring(combined_xml)
links  = [e.get("name") for e in root.findall("link")]
joints = [e.get("name") for e in root.findall("joint")]
print(f"\nLinks  ({len(links)}): {links}")
print(f"Joints ({len(joints)}): {joints}")
print(f"\nOK — written to {OUT}")
