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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from Agent.llm import get_node_timeouts
from Agent.nodes.executor import _EscalateHITLException, escalate_tool  # noqa: F401
from Agent.state import GlobalState
from SkiLib.base import ERROR_ROBOT_INACTIVE, ExecutionPhase, SkillResult
from SkiLib.log import get_logger
from SkiLib.robotcontext import RobotContext
from SkiLib.scenes.fmb import ASSEMBLY_REFERENCE_PATH
from SkiLib.skill_loader import SkillMdLoader

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_ASSEMBLY_REFERENCE = ASSEMBLY_REFERENCE_PATH


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _load_assembly_reference() -> str:
    try:
        return _ASSEMBLY_REFERENCE.read_text(encoding="utf-8")
    except OSError:
        return "(assembly reference unavailable)"


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


class PlanStepInput(BaseModel):
    type:           Literal["action", "check"] = Field(
        description="'action' for primitive execution, 'check' for sensor verification."
    )
    description:    str = Field(description="Short label for this step.")
    primitive:      Optional[str] = Field(
        default=None,
        description="Primitive name for action steps, e.g. MoveJ, MoveL, Grasp, Release.",
    )
    args:           Dict[str, Any] = Field(
        default_factory=dict,
        description="Primitive arguments for action steps.",
    )
    sensor:         Optional[str] = Field(
        default=None,
        description="Sensor tool name for check steps.",
    )
    sensor_args:    Dict[str, Any] = Field(
        default_factory=dict,
        description="Sensor arguments for check steps.",
    )
    check_field:    Optional[str] = Field(
        default=None,
        description="Boolean field in the sensor result to compare.",
    )
    check_expected: Optional[bool] = Field(
        default=None,
        description="Expected boolean value for check_field.",
    )
    on_fail:        str = Field(
        default="llm_recovery",
        description="'llm_recovery' or 'escalate_hitl'.",
    )


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
    primitive_names = {tool.name for tool in primitive_tools}
    sensor_names = {tool.name for tool in sensor_tools}

    class RegisterExecutionPlanSchema(BaseModel):
        plan_steps: List[PlanStepInput] = Field(
            description=(
                "The complete execution plan in strict order. Each item is either "
                "an action step with primitive/args or a check step with sensor fields."
            )
        )

    def _register_execution_plan(plan_steps: List[PlanStepInput]) -> str:
        new_steps: list[ExecutionStep] = []
        for idx, raw in enumerate(plan_steps, start=1):
            if raw.type == "action":
                if not raw.primitive:
                    return f"Plan rejected: action step {idx} is missing primitive."
                if raw.primitive not in primitive_names:
                    return f"Plan rejected: unknown primitive '{raw.primitive}' at step {idx}."
                new_steps.append(ExecutionStep(
                    step_id=idx,
                    type="action",
                    description=raw.description,
                    primitive=raw.primitive,
                    args=raw.args,
                ))
            else:
                if not raw.sensor:
                    return f"Plan rejected: check step {idx} is missing sensor."
                if raw.sensor not in sensor_names:
                    return f"Plan rejected: unknown sensor '{raw.sensor}' at step {idx}."
                if raw.check_field is None or raw.check_expected is None:
                    return f"Plan rejected: check step {idx} is missing check_field/check_expected."
                new_steps.append(ExecutionStep(
                    step_id=idx,
                    type="check",
                    description=raw.description,
                    sensor=raw.sensor,
                    sensor_args=raw.sensor_args,
                    check_field=raw.check_field,
                    check_expected=raw.check_expected,
                    on_fail=raw.on_fail,
                ))

        steps.clear()
        steps.extend(new_steps)
        return f"Registered complete execution plan with {len(steps)} step(s)."

    tools.append(StructuredTool.from_function(
        func=_register_execution_plan,
        name="register_execution_plan",
        description=(
            "Preferred tool: register the entire execution plan in one call. "
            "Use this instead of calling add_<PrimitiveName>_step and "
            "add_<SensorName>_check one by one."
        ),
        args_schema=RegisterExecutionPlanSchema,
    ))

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


def _invoke_with_timeout(llm_with_tools, messages: list, timeout: float, *, phase: str, turn: int | None = None):
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(llm_with_tools.invoke, messages)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout:
        pool.shutdown(wait=False)
        if turn is None:
            logger.error("[executor_v2] %s LLM timed out after %ss", phase, timeout)
        else:
            logger.error("[executor_v2] %s LLM timed out after %ss on turn %d", phase, timeout, turn)
        raise
    finally:
        pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Planning prompt
# ---------------------------------------------------------------------------

def _build_planning_prompt(
    skill_name: str, skill_body: str, params: dict,
    scene_tools: list[str] | None = None,
    retry_context_text: str = "",
) -> str:
    base        = _load_prompt("execution_planner.txt")
    param_lines = "\n".join(f"- `{k}` = `{v}`" for k, v in params.items())
    prompt = (
        f"{base}\n\n"
        f"## Skill: {skill_name}\n\n"
        f"{skill_body}\n\n"
        "## Assembly Reference\n\n"
        f"{_load_assembly_reference()}\n\n"
        "## Concrete Parameter Values\n\n"
        f"{param_lines}"
    )
    if scene_tools:
        tool_lines = "\n".join(f"- {t}" for t in scene_tools)
        prompt += (
            "\n\n## Available Gripper Tools in Scene\n\n"
            "Use one of these exact names for `tool_name` parameters, "
            "or leave `tool_name` empty to use the currently active tool:\n\n"
            f"{tool_lines}"
        )
    if retry_context_text:
        prompt += (
            "\n\n## HITL Retry Background\n\n"
            f"{retry_context_text}"
        )
    return prompt


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
                if isinstance(sensor_result, dict) and sensor_result.get("description"):
                    logger.warning("[executor_v2] Check detail: %s", sensor_result["description"])
                return False, step, sensor_result

        else:
            logger.warning("[executor_v2] Unknown step type '%s' at step %d",
                           step.type, step.step_id)

    return True, None, None


def _failure_info_json(failure_info: Any) -> str:
    payload = (
        failure_info.to_llm_message()
        if isinstance(failure_info, SkillResult)
        else failure_info
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _make_retry_context(
    *,
    task: dict,
    skill_name: str,
    steps: list[ExecutionStep],
    failed_step: ExecutionStep,
    failure_info: Any,
    recovery_conclusion: Any | None = None,
) -> dict:
    ctx = {
        "task_id": task.get("task_id"),
        "skill": skill_name,
        "params": task.get("params", {}),
        "planned_steps": [s.model_dump() for s in steps],
        "failed_step_id": failed_step.step_id,
        "failed_step_description": failed_step.description,
        "failure_info": (
            failure_info.to_llm_message()
            if isinstance(failure_info, SkillResult)
            else failure_info
        ),
    }
    if recovery_conclusion is not None:
        ctx["recovery_conclusion"] = recovery_conclusion
    return ctx


def _summarize_agent_result(result: Any) -> dict:
    """Extract a compact recovery-agent conclusion for later HITL retry prompts."""
    if not isinstance(result, dict):
        return {"result": str(result)}

    messages = result.get("messages") or []
    tail = []
    for msg in messages[-6:]:
        content = getattr(msg, "content", None)
        if content:
            tail.append(str(content))
    return {
        "final_messages": tail,
    }


def _format_retry_context(
    task: dict,
    retry_context: dict | None,
) -> str:
    if not retry_context or retry_context.get("task_id") != task.get("task_id"):
        return ""

    raw_steps = retry_context.get("planned_steps") or []
    failed_id = int(retry_context.get("failed_step_id") or 0)
    try:
        previous_steps = [ExecutionStep.model_validate(step) for step in raw_steps]
        plan_lines = "\n".join(_format_step(step, failed_id) for step in previous_steps)
    except Exception:
        plan_lines = json.dumps(raw_steps, ensure_ascii=False, indent=2, default=str)

    failure_text = json.dumps(
        retry_context.get("failure_info"),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    recovery_conclusion = retry_context.get("recovery_conclusion")
    recovery_text = (
        json.dumps(recovery_conclusion, ensure_ascii=False, indent=2, default=str)
        if recovery_conclusion is not None
        else ""
    )
    completed = [step for step in raw_steps if int(step.get("step_id", 0)) < failed_id]
    return (
        "This executor call is a HITL retry for the same task. The previous "
        "attempt partially executed before failing. Treat the following as "
        "background context, not as a mandatory script: decide the correct "
        "recovery/resume plan using the skill guide and current robot state.\n\n"
        f"Previously failed step: {failed_id} — "
        f"{retry_context.get('failed_step_description', 'unknown')}\n\n"
        f"Completed step ids before failure: {[step.get('step_id') for step in completed]}\n\n"
        "Previous execution plan:\n"
        f"{plan_lines}\n\n"
        "Previous failure result:\n"
        f"```json\n{failure_text}\n```\n\n"
        + (
            "Recovery agent conclusion from the previous attempt:\n"
            f"```json\n{recovery_text}\n```\n\n"
            if recovery_text
            else ""
        )
        + "Important retry rules:\n"
        "- Do not blindly restart from step 1.\n"
        "- First register checks needed to establish current state, especially "
        "`get_attachment_state` / `is_item_grasped` when the prior attempt may "
        "have already grasped the item.\n"
        "- Resume from the earliest still-needed step. Repeating completed "
        "steps is allowed only when the current state proves it is necessary.\n"
    )


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
        "## Assembly Reference\n\n"
        f"{_load_assembly_reference()}\n\n"
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
    retry_context = state.get("retry_context") or {}

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
    # We execute tool calls sequentially ourselves instead of delegating to
    # create_agent's ToolNode, which can run all calls in one AIMessage in
    # parallel and cause non-deterministic step ordering.
    logger.info("[executor_v2] Generating plan for %s(%s)", skill_name, params)
    plan_tools, steps = _make_plan_tools(primitive_tools, sensor_tools)
    plan_tools_by_name = {t.name: t for t in plan_tools}
    from SkiLib.metatools.informative import list_tools
    scene_tools   = list_tools.invoke({})
    retry_context_text = _format_retry_context(task, retry_context)
    if retry_context_text:
        logger.info("[executor_v2] Injecting HITL retry context for %s into planning prompt", tid)
    system_prompt = _build_planning_prompt(
        skill_name,
        spec.body,
        params,
        scene_tools=scene_tools,
        retry_context_text=retry_context_text,
    )

    timeouts  = get_node_timeouts()
    plan_llm  = llm.bind_tools(plan_tools)
    provider  = os.getenv("ROBOSKI_LLM_PROVIDER", "claude").lower()
    messages  = [
        HumanMessage(content=system_prompt),
        HumanMessage(content=(
            f"Build the execution plan for the {skill_name} skill now. "
            "Register every step and check in order using the provided tools. "
            + (
                "This is a HITL retry: use the retry background to decide the "
                "correct recovery/resume plan instead of blindly replaying the "
                "nominal sequence."
                if retry_context_text
                else ""
            )
        )),
    ]

    def _plan_timeout_result() -> dict:
        return {
            "execution_log": [f"[executor_v2] {tid} PLAN TIMEOUT after {timeouts['executor_plan']}s"],
            "last_result": SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type="PLAN_TIMEOUT",
                message=f"Plan generation timed out after {timeouts['executor_plan']}s.",
                suggestion="Retry the task or reduce its complexity.",
                needs_hitl=True,
            ),
            "halt_flag":   True,
            "halt_reason": "TASK_FAILURE",
        }

    if provider == "claude":
        max_turns = 30
        for turn in range(1, max_turns + 1):
            try:
                ai_msg = _invoke_with_timeout(
                    plan_llm, messages, timeouts["executor_plan"],
                    phase=f"Plan for {tid}", turn=turn,
                )
            except FuturesTimeout:
                return _plan_timeout_result()

            messages.append(ai_msg)
            tool_calls = getattr(ai_msg, "tool_calls", []) or []
            if not tool_calls:
                break

            logger.debug("[executor_v2] plan turn=%d, tool_calls=%d", turn, len(tool_calls))
            complete_plan_registered = False
            for tc in tool_calls:
                tool = plan_tools_by_name.get(tc["name"])
                if tool is None:
                    result = f"Unknown execution planning tool: {tc['name']}"
                else:
                    result = tool.invoke(tc["args"])
                    if tc["name"] == "register_execution_plan" and str(result).startswith("Registered complete"):
                        complete_plan_registered = True

                messages.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tc.get("id", tc["name"]),
                ))

            if complete_plan_registered:
                break

            messages.append(HumanMessage(content=(
                "Continue registering any remaining execution-plan steps and checks "
                "in order. If the full plan has already been registered, stop calling tools."
            )))
        else:
            logger.warning("[executor_v2] Reached max plan turns (%d) for %s", max_turns, tid)
    else:
        try:
            ai_msg = _invoke_with_timeout(
                plan_llm, messages, timeouts["executor_plan"],
                phase=f"Plan for {tid}",
            )
        except FuturesTimeout:
            return _plan_timeout_result()

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

    _planned_steps = [s.model_dump() for s in steps]

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
            "retry_context": None,
            "planned_steps": _planned_steps,
            "recovered":     False,
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
            "planned_steps": _planned_steps,
            "retry_context": _make_retry_context(
                task=task,
                skill_name=skill_name,
                steps=steps,
                failed_step=failed_step,  # type: ignore[arg-type]
                failure_info=failure_info,
                recovery_conclusion={
                    "status": "not_run",
                    "message": "Step policy was escalate_hitl, so the recovery agent was not invoked.",
                },
            ),
            "recovered":     False,
        }

    recovery_prompt = _build_recovery_prompt(
        skill_name, spec.body, params,
        steps, failed_step, failure_info,  # type: ignore[arg-type]
    )
    sub_agent_tools = [escalate_tool, *primitive_tools, *sensor_tools]
    sub_agent       = create_agent(model=llm, tools=sub_agent_tools, system_prompt=recovery_prompt)
    recovery_input  = {
        "messages": [HumanMessage(
            content=(
                f"Step {failed_step.step_id} failed during {skill_name} execution. "  # type: ignore[union-attr]
                "Assess current robot state and recover following your system prompt."
            )
        )]
    }

    pool_rec = ThreadPoolExecutor(max_workers=1)
    future   = pool_rec.submit(lambda: sub_agent.invoke(recovery_input))  # type: ignore[arg-type]
    try:
        try:
            recovery_result = future.result(timeout=timeouts["executor_recovery"])
        except FuturesTimeout:
            pool_rec.shutdown(wait=False)
            logger.error("[executor_v2] Recovery LLM timed out after %ss for %s",
                        timeouts["executor_recovery"], tid)
            return {
                "messages": [AIMessage(
                    content=f"[{tid}] {skill_name} | RECOVERY TIMEOUT at step {failed_step.step_id}")],  # type: ignore[union-attr]
                "execution_log": [
                    f"[executor_v2] {tid} RECOVERY TIMEOUT after {timeouts['executor_recovery']}s"
                ],
                "last_result": SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.EXECUTION,
                    error_type="RECOVERY_TIMEOUT",
                    message=f"Recovery agent timed out after {timeouts['executor_recovery']}s.",
                    suggestion="Retry manually or replan the task.",
                    needs_hitl=True,
                ),
                "halt_flag":   True,
                "halt_reason": "TASK_FAILURE",
                "planned_steps": _planned_steps,
                "retry_context": _make_retry_context(
                    task=task,
                    skill_name=skill_name,
                    steps=steps,
                    failed_step=failed_step,  # type: ignore[arg-type]
                    failure_info={
                        "error_type": "RECOVERY_TIMEOUT",
                        "message": f"Recovery agent timed out after {timeouts['executor_recovery']}s.",
                    },
                    recovery_conclusion={
                        "status": "timeout",
                        "message": "Recovery agent did not finish, so no recovery conclusion was produced.",
                    },
                ),
                "recovered":     False,
            }
    except _EscalateHITLException as e:
        pool_rec.shutdown(wait=False)
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
            "planned_steps": _planned_steps,
            "retry_context": _make_retry_context(
                task=task,
                skill_name=skill_name,
                steps=steps,
                failed_step=failed_step,  # type: ignore[arg-type]
                failure_info={
                    "error_type": e.error_type,
                    "message": e.reason or "",
                    "suggestion": e.suggestion,
                },
                recovery_conclusion={
                    "status": "escalated_to_hitl",
                    "error_type": e.error_type,
                    "reason": e.reason or "",
                    "suggestion": e.suggestion,
                },
            ),
            "recovered":     True,
        }
    finally:
        pool_rec.shutdown(wait=False)

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
        "retry_context": None,
        "planned_steps": _planned_steps,
        "recovered":     True,
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
