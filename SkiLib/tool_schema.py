from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, create_model

from SkiLib.base import ERROR_INVALID_PARAM, ExecutionPhase, SkillResult


_PRIMITIVE_TYPES: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}


def build_annotation(param_def: dict[str, Any]) -> Any:
    """Convert one declarative parameter definition to a Python annotation."""
    raw_type = param_def.get("type", "str")
    base_type = _PRIMITIVE_TYPES.get(raw_type, str)

    enum_values = param_def.get("enum")
    if enum_values:
        from typing import Literal

        annotation = Literal[tuple(enum_values)]  # type: ignore[valid-type]
    else:
        annotation = base_type

    if not param_def.get("required", True):
        annotation = Optional[annotation]  # type: ignore[assignment]

    return annotation


def build_pydantic_schema(
    tool_name: str,
    parameters: dict[str, Any],
) -> type[BaseModel]:
    """Build a Pydantic args schema from the shared RoboSki parameter DSL."""
    if not parameters:
        return create_model(f"{tool_name}Params")  # type: ignore[return-value]

    field_definitions: dict[str, Any] = {}
    for field_name, param_def in parameters.items():
        annotation = build_annotation(param_def)
        required = param_def.get("required", True)
        default_val = param_def.get("default", ...)

        if not required and default_val is ...:
            default_val = None

        field_info = Field(
            default=default_val,
            description=param_def.get("description", ""),
        )
        field_definitions[field_name] = (annotation, field_info)

    return create_model(f"{tool_name}Params", **field_definitions)  # type: ignore[call-overload,return-value]


def resolve_tool_kwargs(
    runtime,
    parameters: dict[str, Any],
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any], SkillResult | None]:
    """Resolve LLM-facing string parameters into runtime-native objects."""
    resolved = dict(kwargs)

    for field_name, param_def in parameters.items():
        resolver = param_def.get("resolver")
        if resolver is None or field_name not in resolved or resolved[field_name] is None:
            continue

        raw_value = resolved[field_name]
        if resolver == "target":
            item, err = _resolve_target(runtime, str(raw_value))
        elif resolver == "object":
            item, err = _resolve_object(runtime, str(raw_value))
        elif resolver == "item":
            item, err = _resolve_item(runtime, str(raw_value))
        elif resolver == "tool":
            item, err = _resolve_tool(runtime, str(raw_value))
        else:
            return {}, SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message=(
                    f"Unknown resolver '{resolver}' for parameter '{field_name}'. "
                    "Check the primitive TOOL_PARAMETERS metadata."
                ),
                suggestion="Use one of: target, object, item, tool.",
            )

        if err is not None:
            return {}, err
        resolved[field_name] = item

    return resolved, None


def _resolve_target(runtime, name: str):
    try:
        return runtime.resolve_target(name), None
    except KeyError:
        return None, SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_INVALID_PARAM,
            message=f"Target '{name}' not found in the Genesis scene.",
            suggestion="Use list_targets() to see valid target names.",
        )


def _resolve_object(runtime, name: str):
    try:
        return runtime.resolve_object(name), None
    except KeyError:
        return None, SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_INVALID_PARAM,
            message=f"Object '{name}' not found in the Genesis scene.",
            suggestion="Use list_objects() to see valid object names.",
        )


def _resolve_item(runtime, name: str):
    try:
        return runtime.resolve_item(name), None
    except KeyError:
        return None, SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_INVALID_PARAM,
            message=f"Item '{name}' not found in the Genesis scene.",
            suggestion="Use list_targets() or list_objects() to see valid symbols.",
        )


def _resolve_tool(runtime, name: str):
    if name in runtime.list_tools():
        return name, None
    return None, SkillResult(
        success=False,
        execution_phase=ExecutionPhase.VALIDATION,
        error_type=ERROR_INVALID_PARAM,
        message=f"Tool '{name}' not found in the Genesis scene.",
        suggestion="Use list_tools() to see valid tool names.",
    )
