from langgraph.types import interrupt
from SkiLib.log import get_logger
from Agent.state import GlobalState

logger = get_logger(__name__)


def manual_intervention_handler(state: GlobalState) -> dict:
    task = state.get("current_task", {})
    desc = task.get("description", "No description provided for manual task.")
    logger.info("[manual_handler] Waiting for operator: %s", desc)

    command = interrupt({
        "options":     ["complete", "abort"],
        "description": desc,
    })

    if command == "complete":
        logger.info("[manual_handler] %s -> COMPLETE", task.get("task_id"))
        return {
            "current_task":        {},
            "halt_flag":           False,
            "halt_reason":         None,
            "intervention_action": "complete",
            "execution_log":       [f"[manual_handler] {task.get('task_id')} -> COMPLETE"],
        }
    else:  # abort
        logger.info("[manual_handler] %s -> ABORT", task.get("task_id"))
        return {
            "current_task":        {},
            "todo_list":           [],
            "halt_flag":           False,
            "halt_reason":         None,
            "intervention_action": "abort",
            "execution_log":       [f"[manual_handler] {task.get('task_id')} -> ABORT"],
        }


def manual_intervention_router(state: GlobalState) -> str:
    action = state.get("intervention_action")
    if action == "complete":
        return "dispatcher"
    else:
        if action != "abort":
            logger.warning("[manual_intervention_router] Unexpected action '%s', routing to END", action)
        return "END"
