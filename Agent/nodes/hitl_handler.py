from langchain_core.messages import HumanMessage
from langgraph.types import interrupt
from SkiLib.log import get_logger
from Agent.state import GlobalState

logger = get_logger(__name__)


def hitl_handler(state: GlobalState) -> dict:
    task        = state.get("current_task", {})
    last_result = state.get("last_result")
    tid         = task.get("task_id", "?")

    logger.error("[hitl_handler] Task %s failed: %s",
                 tid, last_result.error_type if last_result else "unknown")
    if last_result and last_result.suggestion:
        logger.info("[hitl_handler] Suggestion: %s", last_result.suggestion)

    command = interrupt({
        "options": ["retry", "next_task", "replan", "abort"],
        "description": (
            f"Task {task.get('task_id')} failed with error: "
            f"{last_result.error_type if last_result else 'unknown'}"
            f"\nMessage: {last_result.message if last_result else 'No details available.'}"
            f"\nSuggestion: {last_result.suggestion if last_result else 'No suggestion available.'}\n"
        ),
    })

    result: dict = {"hitl_command": command}

    if command == "retry":
        logger.info("[hitl_handler] %s -> RETRY", tid)
        result["halt_flag"]     = False
        result["halt_reason"]   = None
        result["execution_log"] = [f"[hitl_handler] {tid} -> RETRY"]
        # current_task preserved — executor retries same task

    elif command == "next_task":
        logger.info("[hitl_handler] %s -> NEXT_TASK (skipped by operator)", tid)
        result["halt_flag"]     = False
        result["halt_reason"]   = None
        result["current_task"]  = {}
        result["execution_log"] = [f"[hitl_handler] {tid} -> NEXT_TASK (skipped)"]

    elif command == "replan":
        logger.info("[hitl_handler] %s -> REPLAN", tid)
        result["halt_flag"]     = False
        result["halt_reason"]   = None
        result["current_task"]  = {}
        result["todo_list"]     = []
        result["messages"]      = [HumanMessage(
            content=f"[HITL] Task {tid} failed. Please review the execution history above and produce a revised plan."
        )]
        result["execution_log"] = [f"[hitl_handler] {tid} -> REPLAN"]

    else:  # abort
        logger.info("[hitl_handler] %s -> ABORT", tid)
        result["halt_flag"]     = False
        result["halt_reason"]   = None
        result["current_task"]  = {}
        result["todo_list"]     = []
        result["execution_log"] = [f"[hitl_handler] {tid} -> ABORT"]

    return result


def hitl_router(state: GlobalState) -> str:
    command = state.get("hitl_command")
    if command == "retry":
        return "executor"
    elif command == "replan":
        return "supervisor"
    elif command == "next_task":
        return "dispatcher"
    else:
        if command not in ("abort", None):
            logger.warning("[hitl_router] Unexpected hitl_command '%s', routing to END", command)
        return "END"
