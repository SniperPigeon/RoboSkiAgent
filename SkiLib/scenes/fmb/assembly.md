# FMB Assembly Reference

This file is the human-readable assembly reference for the Genesis FMB scene.
The editable source of truth is `SkiLib/scenes/fmb/assembly.yaml`;
`SkiLib/genesis/assembly_specs.py` loads and validates that file. This document
mirrors those symbols so an LLM/operator can plan without inventing angles or
target names.

## Default Assembly Order

When the operator only says "assemble", use this order:

1. `Part_A_1`
2. `Part_A_2`
3. `Part_B`
4. `Part_C`

Each part uses the standard target naming pattern:

- pick approach: `<part>_Approach`
- pick target: `<part>_Pick`
- place approach: `<part>_Place_Approach`
- place target: `<part>_Place`

## Angle Semantics

Angles in the assembly spec are object yaw angles unless the field explicitly
says `tcp_yaw`.

- `expected_object_yaw_deg`: desired final object yaw around world Z.
- `grasp_profile`: symbolic choice for how the TCP clamps the part.
- `tcp_yaw_offset_deg`: internal offset from object yaw to TCP yaw.
- `tcp_yaw_deg = object_yaw_deg + tcp_yaw_offset_deg`, normalized by code.

LLMs should choose only the documented `grasp_profile` symbols. They should not
calculate or emit custom yaw values.

## Part Specs

| Part | Default grasp profile | Expected object yaw | Default pick TCP yaw | Default place TCP yaw |
| --- | --- | ---: | ---: | ---: |
| `Part_A_1` | `short_edge` | `0 deg` | `0 deg` | `0 deg` |
| `Part_A_2` | `short_edge` | `0 deg` | `0 deg` | `0 deg` |
| `Part_B` | `long_edge` | `0 deg` | `180 deg` (`-180 deg` normalized) | `180 deg` (`-180 deg` normalized) |
| `Part_C` | `long_edge` | `0 deg` | `90 deg` | `90 deg` |

`Part_B` and `Part_C` keep the previous correct final object yaw (`0 deg`).
Only their default TCP/gripper clamp direction is rotated 90 degrees from the
previous setup. `Part_A_1` and `Part_A_2` are unchanged.

## Grasp Profile Symbols

`Part_A_1`

- `short_edge`: default; TCP yaw follows object yaw.

`Part_A_2`

- `short_edge`: default; TCP yaw follows object yaw.

`Part_B`

- `long_edge`: default; TCP yaw is object yaw plus 180 degrees.
- `short_edge`: previous B-part grasp; TCP yaw is object yaw plus 90 degrees.

`Part_C`

- `long_edge`: default; TCP yaw is object yaw plus 90 degrees.
- `short_edge`: previous C-part grasp; TCP yaw follows object yaw.

## Default PickAndPlace Calls

For the default "assemble" instruction, plan these four `PickAndPlace` tasks:

1. `Part_A_1`: `Home_position`, `Part_A_1_Approach`, `Part_A_1_Pick`,
   `Part_A_1_Place_Approach`, `Part_A_1_Place`
2. `Part_A_2`: `Home_position`, `Part_A_2_Approach`, `Part_A_2_Pick`,
   `Part_A_2_Place_Approach`, `Part_A_2_Place`
3. `Part_B`: `Home_position`, `Part_B_Approach`, `Part_B_Pick`,
   `Part_B_Place_Approach`, `Part_B_Place`
4. `Part_C`: `Home_position`, `Part_C_Approach`, `Part_C_Pick`,
   `Part_C_Place_Approach`, `Part_C_Place`
