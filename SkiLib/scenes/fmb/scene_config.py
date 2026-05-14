from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from SkiLib.scenes.fmb import SCENE_CONFIG_PATH


@dataclass(frozen=True)
class FmbPartSceneSpec:
    name: str
    mesh: str
    stage_x_m: float
    height_m: float
    ref_z_from_bottom_m: float
    place_xy_m: tuple[float, float]
    place_bottom_z_m: float
    color: tuple[float, float, float, float]


@dataclass(frozen=True)
class FmbSceneSpec:
    table_height_m: float
    table_center_x_m: float
    table_size_x_m: float
    table_size_y_m: float
    robot_x_m: float
    board_mesh: str
    board_x_m: float
    board_y_m: float
    board_color: tuple[float, float, float, float]
    stage_y_m: float
    approach_clearance_m: float
    gear_thickness_m: float
    parts: dict[str, FmbPartSceneSpec]


def _as_float_tuple(values: Any, *, length: int, field_name: str) -> tuple[float, ...]:
    if not isinstance(values, list) or len(values) != length:
        raise ValueError(f"scene.yaml: '{field_name}' must be a list of {length} numbers.")
    return tuple(float(v) for v in values)


def load_fmb_scene_spec(path: Path = SCENE_CONFIG_PATH) -> FmbSceneSpec:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"FMB scene config file not found: {path}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"scene.yaml must contain a mapping: {path}")

    table = raw["table"]
    robot = raw["robot"]
    board = raw["board"]
    stage = raw["stage"]
    parts_raw = raw["parts"]
    if not isinstance(parts_raw, dict) or not parts_raw:
        raise ValueError("scene.yaml: 'parts' must be a non-empty mapping.")

    parts: dict[str, FmbPartSceneSpec] = {}
    for name, part_raw in parts_raw.items():
        parts[str(name)] = FmbPartSceneSpec(
            name=str(name),
            mesh=str(part_raw["mesh"]),
            stage_x_m=float(part_raw["stage_x_m"]),
            height_m=float(part_raw["height_m"]),
            ref_z_from_bottom_m=float(part_raw["ref_z_from_bottom_m"]),
            place_xy_m=_as_float_tuple(
                part_raw["place_xy_m"],
                length=2,
                field_name=f"parts.{name}.place_xy_m",
            ),  # type: ignore[arg-type]
            place_bottom_z_m=float(part_raw["place_bottom_z_m"]),
            color=_as_float_tuple(
                part_raw["color"],
                length=4,
                field_name=f"parts.{name}.color",
            ),  # type: ignore[arg-type]
        )

    return FmbSceneSpec(
        table_height_m=float(table["height_m"]),
        table_center_x_m=float(table["center_x_m"]),
        table_size_x_m=float(table["size_x_m"]),
        table_size_y_m=float(table["size_y_m"]),
        robot_x_m=float(robot["x_m"]),
        board_mesh=str(board["mesh"]),
        board_x_m=float(board["x_m"]),
        board_y_m=float(board["y_m"]),
        board_color=_as_float_tuple(board["color"], length=4, field_name="board.color"),  # type: ignore[arg-type]
        stage_y_m=float(stage["y_m"]),
        approach_clearance_m=float(raw["approach_clearance_m"]),
        gear_thickness_m=float(raw["gear_thickness_m"]),
        parts=parts,
    )
