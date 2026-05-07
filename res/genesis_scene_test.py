"""
Thin viewer wrapper around the production build_genesis_scene().
All scene geometry and target definitions live in SkiLib/genesis/scene.py.

Run with: conda run -n rsagent python res/genesis_scene_test.py
"""
import os, platform, sys
from pathlib import Path

# Allow imports from project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if platform.system() == "Darwin":
    os.environ.setdefault("PYOBJUS_MACOS_APPKIT_THREAD_CHECK", "0")

from SkiLib.genesis.scene import build_genesis_scene

bundle = build_genesis_scene(show_viewer=True)
robot  = bundle.robot
scene  = bundle.scene

print("Objects :", list(bundle.objects))
print("Targets :")
for name, t in bundle.targets.items():
    p = t.pose.pos
    print(f"  {name}: ({p[0]:.4f}, {p[1]:.4f}, {p[2]:.4f})")

print("\nHolding home. Ctrl+C to exit.")
try:
    while True:
        robot.control_dofs_position(
            bundle.home_qpos[bundle.arm_dofs], dofs_idx_local=bundle.arm_dofs
        )
        scene.step()
except KeyboardInterrupt:
    print("Stopped.")
