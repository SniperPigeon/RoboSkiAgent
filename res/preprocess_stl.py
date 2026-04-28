"""
Scale STL files from mm to meters and translate so bottom face sits at z=0.
Outputs to res/processed/*.stl
"""
import trimesh
import numpy as np
from pathlib import Path

SRC = Path(__file__).parent
DST = SRC / "processed"
DST.mkdir(exist_ok=True)

for src in sorted(SRC.glob("*.stl")):
    mesh = trimesh.load(str(src))
    # mm -> m
    mesh.apply_scale(0.001)
    # translate so min_z = 0
    mesh.apply_translation([0, 0, -mesh.bounds[0, 2]])
    out = DST / src.name
    mesh.export(str(out))
    print(f"{src.name:30s} -> {mesh.extents[0]:.3f} x {mesh.extents[1]:.3f} x {mesh.extents[2]:.3f} m")

print(f"\nProcessed {len(list(SRC.glob('*.stl')))} files -> {DST}")
