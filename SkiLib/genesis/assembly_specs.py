from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from SkiLib.scenes.fmb import ASSEMBLY_SPEC_PATH


@dataclass(frozen=True)
class GraspProfile:
    """Symbolic grasp choice exposed to LLMs/operators."""

    symbol: str
    description: str
    tcp_yaw_offset_deg: float


@dataclass(frozen=True)
class PartAssemblySpec:
    """Assembly semantics for one FMB part.

    expected_object_yaw_deg is the desired final object yaw in the assembly.
    Grasp profiles describe how the TCP is oriented relative to the object yaw.
    """

    item_name: str
    place_target: str
    expected_object_yaw_deg: float
    default_grasp_profile: str
    grasp_profiles: dict[str, GraspProfile]


def _load_raw_spec(path: Path = ASSEMBLY_SPEC_PATH) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Assembly spec file not found: {path}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Assembly spec file must contain a mapping: {path}")
    return raw


def _parse_assembly_specs(
    raw: dict[str, Any],
) -> tuple[tuple[str, ...], dict[str, PartAssemblySpec]]:
    sequence_raw = raw.get("assembly_sequence")
    parts_raw = raw.get("parts")

    if not isinstance(sequence_raw, list) or not all(isinstance(v, str) for v in sequence_raw):
        raise ValueError("assembly.yaml: 'assembly_sequence' must be a list of part names.")
    if not isinstance(parts_raw, dict):
        raise ValueError("assembly.yaml: 'parts' must be a mapping.")

    specs: dict[str, PartAssemblySpec] = {}
    for item_name, part_raw in parts_raw.items():
        if not isinstance(item_name, str) or not isinstance(part_raw, dict):
            raise ValueError("assembly.yaml: each part entry must be a mapping keyed by part name.")

        profiles_raw = part_raw.get("grasp_profiles")
        if not isinstance(profiles_raw, dict) or not profiles_raw:
            raise ValueError(f"assembly.yaml: part '{item_name}' needs at least one grasp profile.")

        grasp_profiles: dict[str, GraspProfile] = {}
        for symbol, profile_raw in profiles_raw.items():
            if not isinstance(symbol, str) or not isinstance(profile_raw, dict):
                raise ValueError(
                    f"assembly.yaml: grasp profiles for '{item_name}' must be mappings."
                )
            grasp_profiles[symbol] = GraspProfile(
                symbol=symbol,
                description=str(profile_raw.get("description", "")),
                tcp_yaw_offset_deg=float(profile_raw["tcp_yaw_offset_deg"]),
            )

        default_profile = str(part_raw["default_grasp_profile"])
        if default_profile not in grasp_profiles:
            raise ValueError(
                f"assembly.yaml: default grasp profile '{default_profile}' for "
                f"'{item_name}' is not defined."
            )

        specs[item_name] = PartAssemblySpec(
            item_name=item_name,
            place_target=str(part_raw["place_target"]),
            expected_object_yaw_deg=float(part_raw["expected_object_yaw_deg"]),
            default_grasp_profile=default_profile,
            grasp_profiles=grasp_profiles,
        )

    missing = [item for item in sequence_raw if item not in specs]
    if missing:
        raise ValueError(
            "assembly.yaml: assembly_sequence references undefined part(s): "
            f"{missing}"
        )

    return tuple(sequence_raw), specs


ASSEMBLY_SEQUENCE, FMB_ASSEMBLY_SPECS = _parse_assembly_specs(_load_raw_spec())


def normalize_yaw_deg(yaw_deg: float) -> float:
    """Normalize a yaw angle to [-180, 180)."""
    return float((yaw_deg + 180.0) % 360.0 - 180.0)


def assembly_spec_for(item_name: str) -> PartAssemblySpec:
    try:
        return FMB_ASSEMBLY_SPECS[item_name]
    except KeyError as exc:
        raise KeyError(
            f"No assembly spec for '{item_name}'. Available: {sorted(FMB_ASSEMBLY_SPECS)}"
        ) from exc


def grasp_profile_for(item_name: str, profile_symbol: str | None = None) -> GraspProfile:
    spec = assembly_spec_for(item_name)
    symbol = profile_symbol or spec.default_grasp_profile
    if symbol == "default":
        symbol = spec.default_grasp_profile
    try:
        return spec.grasp_profiles[symbol]
    except KeyError as exc:
        raise KeyError(
            f"Invalid grasp profile '{profile_symbol}' for '{item_name}'. "
            f"Available: {sorted(spec.grasp_profiles)}"
        ) from exc


def tcp_yaw_for_object_yaw(
    item_name: str,
    object_yaw_deg: float,
    profile_symbol: str | None = None,
) -> float:
    profile = grasp_profile_for(item_name, profile_symbol)
    return normalize_yaw_deg(float(object_yaw_deg) + profile.tcp_yaw_offset_deg)


def expected_object_yaw_for(item_name: str) -> float:
    return assembly_spec_for(item_name).expected_object_yaw_deg


def default_grasp_profile_symbol(item_name: str) -> str:
    return assembly_spec_for(item_name).default_grasp_profile
