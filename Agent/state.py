from typing import TypedDict, Annotated, Optional, Literal
import operator
from langchain_core.messages import BaseMessage
from SkiLib.base import SkillResult


class GlobalState(TypedDict):
    # Layer-1: planning context
    todo_list: list[dict]           # [{task_id, type, skill/description, params}, ...]

    # Layer-2: execution slot
    current_task: dict              # execution slot: {} = idle, {...} = executing or failed-preserved

    # Robot state snapshot (dict representation of SkiLib.base.RobotState)
    robot_state: dict

    # Control flags
    halt_flag: bool                 # True = all R-skill execution is locked
    halt_reason: Optional[str]      # "TASK_FAILURE" | "MANUAL_TASK" | None

    # Written by Executor; needs_hitl field drives Context Flush routing
    last_result: Optional[SkillResult]

    # Internal routing field for Supervisor: None = proceed to planner, "abort" = skip to END
    supervisor_action: Optional[str]

    plan_review_action: Optional[Literal["approve", "replan", "abort"]]

    # Internal routing field for ManualInterventionHandler: "complete" | "abort"
    intervention_action: Optional[str]

    # Internal routing field for HITLHandler: "retry" | "next_task" | "replan" | "abort"
    hitl_command: Optional[str]

    # Execution log written by Context Flush; Annotated list enables append-only updates
    execution_log: Annotated[list[str], operator.add]

    # LangGraph message list
    messages: Annotated[list[BaseMessage], operator.add]
