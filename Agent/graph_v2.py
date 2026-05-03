"""
graph_v2 — LangGraph state machine using skill.md-based Planner and Executor.

Identical topology to graph.py.  Only two nodes differ:
  - planner   → planner_v2   (tool schemas from SkillMdLoader, not SkillRegistry)
  - executor  → executor_v2  (LLM sub-agent sequences primitives, not Python BaseSkill)

All other nodes (supervisor, plan_review, dispatcher, manual_intervention_handler,
hitl_handler) are reused unchanged from the original graph.

Usage:
    from Agent.graph_v2 import build_graph_v2, make_initial_state
    graph = build_graph_v2()
    state = make_initial_state("把零件 bolt_01 从 PickStation 移到 PlaceStation")
    result = graph.invoke(state, config={"configurable": {"thread_id": "run-1"}})
"""

# Allow `python Agent/graph_v2.py` (direct execution) by ensuring the project
# root is on sys.path before any package imports are attempted.
if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

from functools import partial

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from Agent.state import GlobalState

# ---- Reused nodes (unchanged) ------------------------------------------------
from Agent.nodes.dispatcher import dispatcher, task_router
from Agent.nodes.hitl_handler import hitl_handler, hitl_router
from Agent.nodes.manual_handler import manual_intervention_handler, manual_intervention_router
from Agent.nodes.plan_review import plan_review, plan_review_router
from Agent.nodes.supervisor import supervisor, supervisor_router

# ---- V2 nodes (skill.md-based) -----------------------------------------------
from Agent.nodes.planner_v2 import planner_v2
from Agent.nodes.executor_v2 import executor_v2, post_task_router_v2


def build_graph_v2(
    llm: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Assemble and compile the V2 LangGraph state machine.

    Topology is identical to build_graph().  The only changes are:
      - planner   node uses planner_v2   (skill.md schemas)
      - executor  node uses executor_v2  (LLM sub-agent primitives)

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
            allowed_json_modules=[("SkiLib.base", "SkillResult"), ("SkiLib.base", "ExecutionPhase")]
        )
        checkpointer = MemorySaver(serde=serde)

    builder = StateGraph(GlobalState)

    # ---- Nodes ----------------------------------------------------------------
    builder.add_node("supervisor",                  partial(supervisor,    llm=llm))
    builder.add_node("planner",                     partial(planner_v2,    llm=llm))   # V2
    builder.add_node("plan_review",                 plan_review)
    builder.add_node("dispatcher",                  dispatcher)
    builder.add_node("manual_intervention_handler", manual_intervention_handler)
    builder.add_node("executor",                    partial(executor_v2,   llm=llm))   # V2
    builder.add_node("hitl_handler",                hitl_handler)

    # ---- Unconditional edges --------------------------------------------------
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", supervisor_router, {
        "planner": "planner",
        "END":     END,
    })
    builder.add_edge("planner", "plan_review")

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

    builder.add_conditional_edges("executor", post_task_router_v2, {   # V2 router
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


from Agent.graph import make_initial_state as make_initial_state  # noqa: F401


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    print("Building graph_v2 (no LLM / RoboDK required for topology)...")
    graph = build_graph_v2()

    # Render PNG via Mermaid (mirrors the notebook pattern:
    #   display(Image(graph.get_graph().draw_mermaid_png())) )
    png_bytes = graph.get_graph().draw_mermaid_png()

    # If an output path is given as the first argument, save there.
    # Otherwise write to a temp file and open with the system viewer.
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
        out.write_bytes(png_bytes)
        print(f"Graph PNG saved to {out}")
    else:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            tmp = f.name
        print(f"Opening graph preview: {tmp}")
        import subprocess
        opener = {"win32": "start", "darwin": "open"}.get(sys.platform, "xdg-open")
        subprocess.run([opener, tmp], shell=(sys.platform == "win32"), check=False)
