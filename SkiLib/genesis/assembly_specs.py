from __future__ import annotations

from dataclasses import dataclass


ASSEMBLY_SEQUENCE: tuple[str, ...] = ("Part_A_1", "Part_A_2", "Part_B", "Part_C")


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


FMB_ASSEMBLY_SPECS: dict[str, PartAssemblySpec] = {
    "Part_A_1": PartAssemblySpec(
        item_name="Part_A_1",
        place_target="Part_A_1_Place",
        expected_object_yaw_deg=0.0,
        default_grasp_profile="short_edge",
        grasp_profiles={
            "short_edge": GraspProfile(
                symbol="short_edge",
                description="Default A-part grasp; TCP yaw follows the object yaw.",
                tcp_yaw_offset_deg=0.0,
            ),
        },
    ),
    "Part_A_2": PartAssemblySpec(
        item_name="Part_A_2",
        place_target="Part_A_2_Place",
        expected_object_yaw_deg=0.0,
        default_grasp_profile="short_edge",
        grasp_profiles={
            "short_edge": GraspProfile(
                symbol="short_edge",
                description="Default A-part grasp; TCP yaw follows the object yaw.",
                tcp_yaw_offset_deg=0.0,
            ),
        },
    ),
    "Part_B": PartAssemblySpec(
        item_name="Part_B",
        place_target="Part_B_Place",
        expected_object_yaw_deg=0.0,
        default_grasp_profile="long_edge",
        grasp_profiles={
            "long_edge": GraspProfile(
                symbol="long_edge",
                description="Default B-part grasp rotated 90 deg from the previous setup; TCP yaw is object yaw plus 180 deg.",
                tcp_yaw_offset_deg=180.0,
            ),
            "short_edge": GraspProfile(
                symbol="short_edge",
                description="Previous B-part grasp; TCP yaw is object yaw plus 90 deg.",
                tcp_yaw_offset_deg=90.0,
            ),
        },
    ),
    "Part_C": PartAssemblySpec(
        item_name="Part_C",
        place_target="Part_C_Place",
        expected_object_yaw_deg=0.0,
        default_grasp_profile="long_edge",
        grasp_profiles={
            "short_edge": GraspProfile(
                symbol="short_edge",
                description="Previous C-part grasp; TCP yaw follows the object yaw.",
                tcp_yaw_offset_deg=0.0,
            ),
            "long_edge": GraspProfile(
                symbol="long_edge",
                description="Default C-part grasp rotated 90 deg from the previous setup; TCP yaw is object yaw plus 90 deg.",
                tcp_yaw_offset_deg=90.0,
            ),
        },
    ),
}


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
