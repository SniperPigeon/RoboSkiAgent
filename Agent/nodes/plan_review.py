from langchain_core.messages import HumanMessage
from langgraph.types import interrupt
from SkiLib.log import get_logger
from Agent.state import GlobalState

logger = get_logger(__name__)


def plan_review(state: GlobalState) -> dict:
    logger.info("[plan_review] Reviewing plan...")
    plan_summary = "\n".join(
        f"  {t['task_id']} [{t['type']}] "
        + (t.get("skill", "") + str(t.get("params", {})) if t["type"] == "auto" else t.get("description", ""))
        for t in state.get("todo_list", [])
    )

    result = interrupt({
        "options": ["approve", "replan", "abort"],
        "description": f"Generated plan:\n{plan_summary}",
    })

    if isinstance(result, dict):   # replan path carries feedback
        command  = result.get("action")
        feedback = result.get("feedback", "")
    else:                           # approve / abort are plain strings
        command  = result
        feedback = ""

    return_state: dict = {
        "execution_log":     [f"[plan_review] Plan reviewed result: {command}"],
        "plan_review_action": command,
    }
    if command == "replan":
        return_state["messages"]  = [HumanMessage(content=f"Please replan based on human feedback: {feedback}")]
        return_state["todo_list"] = []
    return return_state


def plan_review_router(state: GlobalState) -> str:
    action = state.get("plan_review_action")
    if action == "approve":
        return "approve"
    elif action == "replan":
        return "replan"
    else:
        return "END"
