from SkiLib.log import get_logger
from Agent.state import GlobalState

logger = get_logger(__name__)


def dispatcher(state: GlobalState) -> dict:
    current = state.get("current_task")
    if current and current.get("task_id") is not None:
        logger.info("[dispatcher] Slot occupied: %s, skip pop", current["task_id"])
        return {}
    if not state.get("todo_list"):
        logger.info("[dispatcher] No tasks in todo_list")
        return {}

    next_task = state["todo_list"][0]
    label = next_task.get("skill") or next_task.get("description", "?")
    logger.info("[dispatcher] Dispatching %s (%s): %s",
                next_task["task_id"], next_task["type"], label)

    updates: dict = {
        "current_task": next_task,
        "todo_list":    state["todo_list"][1:],
        "execution_log": [f"[dispatcher] {next_task['task_id']} ({next_task['type']}): {label}"],
    }

    # Manual tasks: set halt so task_router routes to manual_intervention_handler
    if next_task["type"] == "manual":
        updates["halt_flag"]   = True
        updates["halt_reason"] = "MANUAL_TASK"

    return updates


def task_router(state: GlobalState) -> str:
    current = state.get("current_task")
    if not current or current.get("task_id") is None:
        return "END"
    return current["type"]  # "auto" or "manual"
