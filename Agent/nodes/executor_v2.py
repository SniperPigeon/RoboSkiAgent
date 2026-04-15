"""
executor_v2 — Plan-then-execute hybrid executor.

Design
------
1. **Plan phase  (LLM, tool calling)**
   The LLM reads skill.md and calls dynamically-generated tools to register
   a concrete execution plan.  Tool names mirror the Planner pattern:
     add_<PrimitiveName>_step  — derived from primitive_registry.as_tools()
     add_<SensorName>_check    — derived from sensor_registry.get_tools()
   No hardcoding: new primitives or sensors appear automatically.

2. **Execute phase  (Python, step by step)**
   The executor iterates through the registered steps:
   - ActionStep → primitive.try_execute(**args) directly
   - CheckStep  → sensor_tool.invoke(sensor_args), compare result[check_field]
   If a step fails the LLM recovery sub-agent is invoked.

3. **Recovery phase  (LLM sub-agent, on demand)**
   Only triggered on failure.  The sub-agent receives skill.md + concrete
   params + failed step context, then calls primitives/sensors directly.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from Agent.nodes.executor import _EscalateHITLException, escalate_tool  # noqa: F401
from Agent.state import GlobalState
from SkiLib.base import ERROR_ROBOT_INACTIVE, ExecutionPhase, SkillResult
from SkiLib.log import get_logger
from SkiLib.robotcontext import RobotContext
from SkiLib.skill_loader import SkillMdLoader

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Execution step data class
# ---------------------------------------------------------------------------

class ExecutionStep(BaseModel):
    step_id:        int
    type:           Literal["action", "check"]
    description:    str
    # Action fields
    primitive:      Optional[str]   = None
    args:           Dict[str, Any]  = {}
    # Check fields
    sensor:         Optional[str]   = None
    sensor_args:    Dict[str, Any]  = {}
    check_field:    Optional[str]   = None
    check_expected: Optional[bool]  = None
    on_fail:        str             = "llm_recovery"


# ---------------------------------------------------------------------------
# Dynamic plan-building tool generation
# ---------------------------------------------------------------------------

def _make_plan_tools(
    primitive_tools: list,
    sensor_tools: list,
) -> tuple[list[StructuredTool], list[ExecutionStep]]:
    """
    Derive add_<Name>_step and add_<Name>_check tools from the live registries.

    primitive_tools : from primitive_registry.as_tools()
    sensor_tools    : from sensor_registry.get_tools()

    Returns (tools, steps) where steps is mutated in-place by tool calls.
    """
    steps: list[ExecutionStep] = []

    def _next_id() -> int:
        return len(steps) + 1

    tools: list[StructuredTool] = []

    # ---- Action step tools — one per primitive --------------------------------
    for prim_tool in primitive_tools:
        prim_name = prim_tool.name

        # Extend the existing primitive args_schema with an optional description.
        base_schema = prim_tool.args_schema
        if base_schema is not None:
            StepSchema = create_model(
                f"Add{prim_name}StepSchema",
                __base__=base_schema,
                description=(str, Field(default=prim_name,
                                        description="Short label for this step.")),
            )
        else:
            StepSchema = create_model(
                f"Add{prim_name}StepSchema",
                description=(str, Field(default=prim_name,
                                        description="Short label for this step.")),
            )

        def _make_adder(name: str):
            def _add_step(**kwargs) -> str:
                desc = kwargs.pop("description", name)
                s = ExecutionStep(
                    step_id=_next_id(), type="action",
                    description=desc, primitive=name, args=kwargs,
                )
                steps.append(s)
                return (f"Step {s.step_id}: {name}({kwargs}) registered. "
                        f"Plan has {len(steps)} step(s).")
            return _add_step

        tools.append(StructuredTool(
            name=f"add_{prim_name}_step",
            description=(
                f"Register a {prim_name} step in the execution plan. "
                + (prim_tool.description or "")
            ),
            func=_make_adder(prim_name),
            args_schema=StepSchema,
        ))

    # ---- Check step tools — one per sensor ------------------------------------
    for sensor_tool in sensor_tools:
        sensor_name = sensor_tool.name

        # Extend the sensor's args_schema with check-specific fields.
        base_schema = sensor_tool.args_schema
        check_extra: Dict[str, Any] = {
            "check_field": (
                str,
                Field(description=(
                    "Key in the sensor return dict to evaluate as bool. "
                    "See this tool's description for the available fields."
                )),
            ),
            "check_expected": (
                bool,
                Field(description="Expected value: true or false."),
            ),
            "on_fail": (
                str,
                Field(default="llm_recovery",
                      description="'llm_recovery' or 'escalate_hitl'."),
            ),
            "description": (
                str,
                Field(default=f"Check {sensor_name}",
                      description="Short label for this check step."),
            ),
        }
        if base_schema is not None:
            CheckSchema = create_model(
                f"Add{sensor_name}CheckSchema",
                __base__=base_schema,
                **check_extra,
            )
        else:
            CheckSchema = create_model(
                f"Add{sensor_name}CheckSchema",
                **check_extra,
            )

        def _make_check_adder(name: str):
            def _add_check(**kwargs) -> str:
                check_field    = kwargs.pop("check_field")
                check_expected = kwargs.pop("check_expected")
                on_fail        = kwargs.pop("on_fail", "llm_recovery")
                desc           = kwargs.pop("description", f"Check {name}")
                # remaining kwargs → sensor call args
                s = ExecutionStep(
                    step_id=_next_id(), type="check",
                    description=desc, sensor=name, sensor_args=kwargs,
                    check_field=check_field, check_expected=check_expected,
                    on_fail=on_fail,
                )
                steps.append(s)
                return (f"Step {s.step_id}: CHECK {name}.{check_field}=="
                        f"{check_expected} registered.")
            return _add_check

        tools.append(StructuredTool(
            name=f"add_{sensor_name}_check",
            description=(
                f"Register a sensor check using {sensor_name}(). "
                + (sensor_tool.description or "")
            ),
            func=_make_check_adder(sensor_name),
            args_schema=CheckSchema,
        ))

    return tools, steps


# ---------------------------------------------------------------------------
# Planning prompt
# ---------------------------------------------------------------------------

def _build_planning_prompt(skill_name: str, skill_body: str, params: dict) -> str:
    base        = _load_prompt("execution_planner.txt")
    param_lines = "\n".join(f"- `{k}` = `{v}`" for k, v in params.items())
    return (
        f"{base}\n\n"
        f"## Skill: {skill_name}\n\n"
        f"{skill_body}\n\n"
        "## Concrete Parameter Values\n\n"
        f"{param_lines}"
    )


# ---------------------------------------------------------------------------
# Plan execution (Python, step by step)
# ---------------------------------------------------------------------------

def _run_plan(
    steps: list[ExecutionStep],
    primitive_tools_by_name: dict,
    sensor_by_name: dict,
) -> tuple[bool, Optional[ExecutionStep], Optional[Any]]:
    """
    Execute the plan steps in order using the wrapper tools from
    primitive_registry.as_tools().

    Wrapper tools already handle str → RoboDK Item resolution internally,
    so no separate symbol resolution is needed here.  They return a dict
    (from SkillResult.to_llm_message()), which is passed directly to the
    recovery path — _build_recovery_prompt handles both dict and SkillResult.

    Returns:
        (True,  None,         None)       — all steps passed
        (False, failed_step,  dict)       — a step failed
    """
    for step in steps:
        if step.type == "action":
            tool = primitive_tools_by_name.get(step.primitive)
            if tool is None:
                logger.error("[executor_v2] Unknown primitive '%s'", step.primitive)
                return False, step, {
                    "success": False,
                    "phase": ExecutionPhase.VALIDATION.value,
                    "error_type": "UNKNOWN_PRIMITIVE",
                    "message": f"Primitive '{step.primitive}' not in registry.",
                    "needs_hitl": True,
                }
            logger.info("[executor_v2] Step %d: %s(%s)", step.step_id, step.primitive, step.args)
            result: dict = tool.invoke(step.args)
            if not result.get("success"):
                logger.warning("[executor_v2] Step %d (%s) FAILED: %s",
                               step.step_id, step.primitive, result.get("error_type"))
                return False, step, result

        elif step.type == "check":
            sensor_fn = sensor_by_name.get(step.sensor)
            if sensor_fn is None:
                logger.error("[executor_v2] Unknown sensor '%s'", step.sensor)
                return False, step, {
                    "error": f"Sensor '{step.sensor}' not found in registry.",
                    step.check_field or "value": None,
                }
            logger.info("[executor_v2] Step %d: CHECK %s(%s) expect %s=%s",
                        step.step_id, step.sensor, step.sensor_args,
                        step.check_field, step.check_expected)
            sensor_result: dict = sensor_fn.invoke(step.sensor_args)
            if sensor_result.get(step.check_field) != step.check_expected:
                logger.warning("[executor_v2] Step %d CHECK FAILED: %s.%s expected=%s got=%s",
                               step.step_id, step.sensor, step.check_field,
                               step.check_expected, sensor_result.get(step.check_field))
                return False, step, sensor_result

        else:
            logger.warning("[executor_v2] Unknown step type '%s' at step %d",
                           step.type, step.step_id)

    return True, None, None


# ---------------------------------------------------------------------------
# Recovery prompt
# ---------------------------------------------------------------------------

def _format_step(s: ExecutionStep, failed_id: int) -> str:
    marker = " ◄ FAILED" if s.step_id == failed_id else ""
    if s.type == "action":
        return f"  {s.step_id}. [action] {s.primitive}({s.args}) — {s.description}{marker}"
    else:
        return (
            f"  {s.step_id}. [check]  {s.sensor}({s.sensor_args}) "
            f"expect {s.check_field}={s.check_expected} — {s.description}{marker}"
        )


def _build_recovery_prompt(
    skill_name: str,
    skill_body: str,
    params: dict,
    steps: list[ExecutionStep],
    failed_step: ExecutionStep,
    failure_info: Any,
) -> str:
    base        = _load_prompt("skill_executor.txt")
    param_lines = "\n".join(f"- `{k}` = `{v}`" for k, v in params.items())
    failure_text = (
        json.dumps(failure_info.to_llm_message(), ensure_ascii=False, indent=2)
        if isinstance(failure_info, SkillResult)
        else json.dumps(failure_info, ensure_ascii=False, indent=2)
    )
    step_detail = (
        f"Failed primitive: `{failed_step.primitive}` with args `{failed_step.args}`"
        if failed_step.type == "action" else
        f"Failed sensor check: `{failed_step.sensor}` "
        f"expected `{failed_step.check_field}`={failed_step.check_expected}"
    )
    plan_lines = "\n".join(_format_step(s, failed_step.step_id) for s in steps)
    return (
        f"{base}\n\n"
        f"## Skill: {skill_name}\n\n"
        f"{skill_body}\n\n"
        "## Concrete Parameter Values\n\n"
        f"{param_lines}\n\n"
        "## Full Execution Plan\n\n"
        "The plan that was generated and partially executed (steps before the failed "
        "one have already completed successfully):\n\n"
        f"{plan_lines}\n\n"
        "## Failure Context\n\n"
        f"Execution halted at **step {failed_step.step_id}**: "
        f"_{failed_step.description}_\n\n"
        f"{step_detail}\n\n"
        f"**Failure result:**\n```json\n{failure_text}\n```\n\n"
        "**Your job:**\n"
        "1. Call `get_attachment_state()` to confirm the current gripper state.\n"
        "2. Using the Full Execution Plan above, identify which step to resume from "
        "   (steps before the failed one have already been executed).\n"
        "3. Follow the Recovery Hints for the specific error type.\n"
        "4. Resume from the right step — do NOT repeat already-completed steps "
        "   unless Recovery Hints explicitly require it.\n"
        "5. Call `escalate_to_hitl` if recovery is exhausted or the error is unrecoverable."
    )


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

def executor_v2(state: GlobalState, *, llm: BaseChatModel) -> dict:
    """
    Executor node (V2 plan-then-execute).

    State reads:  current_task, halt_flag
    State writes: last_result, current_task (cleared on success), halt_flag,
                  halt_reason, messages, execution_log
    """
    task       = state.get("current_task", {})
    tid        = task.get("task_id", "?")
    skill_name = task.get("skill", "")
    params     = task.get("params", {})

    # ---- Halt guard -----------------------------------------------------------
    if state.get("halt_flag"):
        logger.warning("[executor_v2] Halted — skipping %s", tid)
        return {
            "execution_log": [f"[executor_v2] HALTED — skipping {tid}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_ROBOT_INACTIVE,
                message="Robot is halted. Executor skipped task.",
                needs_hitl=True,
            ),
        }

    # ---- Load skill spec -------------------------------------------------------
    loader = SkillMdLoader.instance()
    if not loader.has(skill_name):
        logger.error("[executor_v2] No skill.md for '%s'", skill_name)
        return {
            "execution_log": [f"[executor_v2] {tid} NO SKILL SPEC: {skill_name}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type="SKILL_NOT_FOUND",
                message=f"No skill.md found for '{skill_name}'.",
                suggestion="Add a <SkillName>.md or use a manual task.",
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }
    spec = loader.get(skill_name)

    # ---- Resolve runtime registries -------------------------------------------
    ctx = RobotContext.instance()
    if ctx is None:
        logger.error("[executor_v2] RobotContext not initialized")
        return {
            "execution_log": [f"[executor_v2] {tid} NO ROBOT CONTEXT"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type="ROBOT_INACTIVE",
                message="RobotContext has not been initialized.",
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    primitive_tools         = ctx.primitive_registry.as_tools()
    primitive_tools_by_name = {t.name: t for t in primitive_tools}
    sensor_tools            = ctx.sensor_tools
    sensor_by_name          = {t.name: t for t in sensor_tools}

    # ---- Phase 1: generate execution plan via tool calls (LLM) ----------------
    # We call the LLM once and execute its tool_calls sequentially ourselves
    # instead of delegating to create_agent's ToolNode, which runs all calls in
    # a single AIMessage in parallel and causes non-deterministic step ordering.
    logger.info("[executor_v2] Generating plan for %s(%s)", skill_name, params)
    plan_tools, steps = _make_plan_tools(primitive_tools, sensor_tools)
    plan_tools_by_name = {t.name: t for t in plan_tools}
    system_prompt      = _build_planning_prompt(skill_name, spec.body, params)

    plan_llm = llm.bind_tools(plan_tools)
    ai_msg   = plan_llm.invoke([
        HumanMessage(content=system_prompt),
        HumanMessage(content=(
            f"Build the execution plan for the {skill_name} skill now. "
            "Register every step and check in order using the provided tools."
        )),
    ])
    for tc in getattr(ai_msg, "tool_calls", []):
        tool = plan_tools_by_name.get(tc["name"])
        if tool is not None:
            tool.invoke(tc["args"])

    if not steps:
        logger.error("[executor_v2] Plan agent produced no steps for %s", skill_name)
        return {
            "execution_log": [f"[executor_v2] {tid} EMPTY PLAN"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type="EMPTY_PLAN",
                message="Plan agent completed without registering any steps.",
                suggestion="Check skill.md format and execution_planner.txt.",
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    logger.info("[executor_v2] Plan ready: %d step(s) — %s",
                len(steps), [f"{s.step_id}:{s.type}:{s.primitive or s.sensor}" for s in steps])

    # ---- Phase 2: execute plan step by step (Python) --------------------------
    success, failed_step, failure_info = _run_plan(steps, primitive_tools_by_name, sensor_by_name)

    if success:
        logger.info("[executor_v2] %s (%s) -> SUCCESS (code path)", tid, skill_name)
        return {
            "messages":      [AIMessage(content=f"[{tid}] {skill_name} | SUCCESS")],
            "execution_log": [f"[executor_v2] {tid} {skill_name} -> SUCCESS"],
            "last_result": SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                message=f"{skill_name} completed by plan executor.",
            ),
            "current_task": {},
        }

    # ---- Phase 3: recovery (LLM sub-agent, on demand) -------------------------
    logger.warning("[executor_v2] %s failed at step %d (%s) — starting LLM recovery",
                   tid, failed_step.step_id, failed_step.description)  # type: ignore[union-attr]

    if failed_step.on_fail == "escalate_hitl":  # type: ignore[union-attr]
        err_type = (failure_info.error_type if isinstance(failure_info, SkillResult)
                    else "CHECK_FAILED")
        msg      = (failure_info.message if isinstance(failure_info, SkillResult)
                    else str(failure_info))
        return {
            "messages": [AIMessage(
                content=f"[{tid}] {skill_name} | ESCALATED at step {failed_step.step_id}")],  # type: ignore[union-attr]
            "execution_log": [f"[executor_v2] {tid} {skill_name} -> ESCALATED: {err_type}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=err_type or "STEP_FAILURE",
                message=msg,
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    recovery_prompt = _build_recovery_prompt(
        skill_name, spec.body, params,
        steps, failed_step, failure_info,  # type: ignore[arg-type]
    )
    sub_agent_tools = [escalate_tool, *primitive_tools, *sensor_tools]
    sub_agent       = create_agent(model=llm, tools=sub_agent_tools, system_prompt=recovery_prompt)

    try:
        sub_agent.invoke({
            "messages": [HumanMessage(
                content=(
                    f"Step {failed_step.step_id} failed during {skill_name} execution. "  # type: ignore[union-attr]
                    "Assess current robot state and recover following your system prompt."
                )
            )]
        })

    except _EscalateHITLException as e:
        logger.error("[executor_v2] %s escalated to HITL: %s", tid, e.error_type)
        return {
            "messages": [AIMessage(
                content=f"[{tid}] {skill_name} | FAILED | error: {e.error_type} | suggestion: {e.suggestion or 'N/A'}"
            )],
            "execution_log": [f"[executor_v2] {tid} {skill_name} -> ESCALATED: {e.error_type}"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=e.error_type,
                message=e.reason or "",
                suggestion=e.suggestion,
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    logger.info("[executor_v2] %s (%s) -> RECOVERED by LLM", tid, skill_name)
    return {
        "messages": [AIMessage(content=f"[{tid}] {skill_name} | RECOVERED")],
        "execution_log": [f"[executor_v2] {tid} {skill_name} -> RECOVERED"],
        "last_result": SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            message=f"{skill_name} recovered by LLM after step {failed_step.step_id} failure.",  # type: ignore[union-attr]
        ),
        "current_task": {},
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def post_task_router_v2(state: GlobalState) -> str:
    last_result: SkillResult | None = state.get("last_result")
    if last_result is None:
        logger.warning("[post_task_router_v2] last_result is None, routing to END")
        return "END"
    return "dispatcher" if last_result.success else "hitl_handler"
