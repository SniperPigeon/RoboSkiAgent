from functools import partial

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from Agent.state import GlobalState
from Agent.nodes.dispatcher import dispatcher, task_router
from Agent.nodes.executor import executor, post_task_router
from Agent.nodes.hitl_handler import hitl_handler, hitl_router
from Agent.nodes.manual_handler import manual_intervention_handler, manual_intervention_router
from Agent.nodes.plan_review import plan_review, plan_review_router
from Agent.nodes.planner import planner
from Agent.nodes.supervisor import supervisor


def build_graph(
    llm: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Assemble and compile the full LangGraph state machine.

    Args:
        llm:          LangChain chat model. Defaults to create_llm() if None.
        checkpointer: LangGraph checkpoint saver. Defaults to in-memory MemorySaver.

    Returns:
        CompiledStateGraph ready for invoke() / stream().
    """
    if llm is None:
        from Agent.llm import create_llm
        llm = create_llm()

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[("SkiLib.base", "SkillResult"), ("SkiLib.base", "ExecutionPhase")]
        )
        checkpointer = MemorySaver(serde=serde)

    builder = StateGraph(GlobalState)

    # ---- Nodes ----------------------------------------------------------------
    builder.add_node("supervisor",                  partial(supervisor, llm=llm))
    builder.add_node("planner",                     partial(planner,    llm=llm))
    builder.add_node("plan_review",                 plan_review)
    builder.add_node("dispatcher",                  dispatcher)
    builder.add_node("manual_intervention_handler", manual_intervention_handler)
    builder.add_node("executor",                    partial(executor,   llm=llm))
    builder.add_node("hitl_handler",                hitl_handler)

    # ---- Unconditional edges --------------------------------------------------
    builder.add_edge(START,        "supervisor")
    builder.add_edge("supervisor", "planner")
    builder.add_edge("planner",    "plan_review")

    # ---- Conditional edges ----------------------------------------------------
    builder.add_conditional_edges("plan_review", plan_review_router, {
        "approve": "dispatcher",
        "replan":  "supervisor",
        "END":     END,
    })

    builder.add_conditional_edges("dispatcher", task_router, {
        "auto":   "executor",
        "manual": "manual_intervention_handler",
        "END":    END,
    })

    builder.add_conditional_edges("manual_intervention_handler", manual_intervention_router, {
        "dispatcher": "dispatcher",
        "END":        END,
    })

    builder.add_conditional_edges("executor", post_task_router, {
        "dispatcher":   "dispatcher",
        "hitl_handler": "hitl_handler",
        "END":          END,
    })

    builder.add_conditional_edges("hitl_handler", hitl_router, {
        "executor":   "executor",
        "supervisor": "supervisor",
        "dispatcher": "dispatcher",
        "END":        END,
    })

    return builder.compile(checkpointer=checkpointer)


def make_initial_state(prompt: str) -> dict:
    """Build the initial GlobalState dict for a new run."""
    return {
        "messages":            [HumanMessage(content=prompt)],
        "todo_list":           [],
        "current_task":        {},
        "robot_state":         {},
        "halt_flag":           False,
        "halt_reason":         None,
        "last_result":         None,
        "intervention_action": None,
        "hitl_command":        None,
        "execution_log":       [],
    }
