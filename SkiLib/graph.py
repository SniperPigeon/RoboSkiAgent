"""
LangGraph Multi-Agent State Machine for RoboSkiAgent

Implements the Plan-and-Execute architecture with two layers:
- Layer 1 (Planning): Supervisor → Planner
- Layer 2 (Execution): Dispatcher → Executor → Context Flush (loop)

Usage:
    from SkiLib.graph import create_graph
    app = create_graph()
    result = app.invoke(initial_state)

For LangGraph Studio visualization, this module is registered in `langgraph.json`.
"""

from typing import TypedDict, Annotated, Optional
import operator

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END


# ══════════════════════════════════════════════════════════════════════════════
# GlobalState Definition
# ══════════════════════════════════════════════════════════════════════════════

class GlobalState(TypedDict):
    """
    Shared state across all agents in the LangGraph workflow.
    
    Aligned with CLAUDE.md specification.
    Production version should import RobotState from SkiLib.base.
    """
    # Layer-1: planning outputs
    todo_list: list[dict]           # Planner generates [{task_id, skill, params}, ...]
    
    # Layer-2: execution context
    current_task: dict              # Execution slot: {} = idle, {...} = in-flight or failed
    
    # Robot runtime snapshot (stub — production type: SkiLib.base.RobotState)
    robot_state: dict
    
    # Control flags
    halt_flag: bool                 # True = all R-skill execution locked (HILP trigger)
    
    # Executor writes result here; Context Flush uses this to decide success/failure path
    last_result: Optional[dict]
    
    # Audit trail written by Context Flush; Annotated list avoids key overwrite
    execution_log: Annotated[list[str], operator.add]
    
    # LangGraph message bus
    messages: Annotated[list[BaseMessage], operator.add]


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1: Planning Nodes
# ══════════════════════════════════════════════════════════════════════════════

def supervisor(state: GlobalState) -> dict:
    """
    Supervisor Node (LLM-driven)
    
    Role: Gather domain knowledge, resolve ambiguities via task-skills.
    Rule: Never compute coordinates; only handle symbols (Target_A, Tool_Gripper).
    
    TODO: Replace stub with real ReAct loop + task-skills integrated with SkiLib.
    """
    print("[supervisor] Analyzing instruction...")
    last_msg = state["messages"][-1].content if state["messages"] else "(no input)"
    
    return {
        "messages": [AIMessage(content=f"[Supervisor] Instruction understood: {last_msg}")]
    }


def planner(state: GlobalState) -> dict:
    """
    Planner Node (LLM-driven with Structured Output)
    
    Role: Emit structured todo_list JSON via forced structured output.
    Rule: Output must be valid JSON; add schema validation + retry in production.
    
    TODO: Replace stub with LLM structured output + Pydantic schema + retry logic.
    """
    print("[planner] Generating task plan...")
    
    # STUB: hardcoded task queue for demonstration
    todo = [
        {"task_id": "t1", "skill": "MoveJ",       "params": {"target": "Home"}},
        {"task_id": "t2", "skill": "PickAndPlace", "params": {"pick": "Part_A", "place": "Tray_1"}},
        {"task_id": "t3", "skill": "MoveJ",       "params": {"target": "Home"}},
    ]
    
    return {
        "todo_list": todo,
        "messages":  [AIMessage(content=f"[Planner] {len(todo)} tasks queued")],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Layer 2: Execution Nodes
# ══════════════════════════════════════════════════════════════════════════════

def dispatcher(state: GlobalState) -> dict:
    """
    Dispatcher Node (Pure Code, No LLM)
    
    Role: Fill the execution slot — pop todo_list[0] into current_task ONLY when slot is empty.
          If current_task is already set (e.g. retained after failure), do nothing.
    Rule: 100% deterministic; must never invoke any LLM.
    
    State transitions for `current_task` slot:
    - {} → {...}  : Slot is empty, pop next task from todo_list
    - {...} → {...}: Slot occupied (task in-flight or retained after failure), skip
    """
    if state.get("current_task"):
        # Slot occupied — task in flight or retained after failure; do not overwrite
        print(f"[dispatcher] Slot occupied: {state['current_task'].get('task_id')} — skipping pop.")
        return {}
    
    todo = list(state.get("todo_list", []))
    if not todo:
        print("[dispatcher] todo_list empty — nothing to dispatch.")
        return {}
    
    task = todo.pop(0)
    print(f"[dispatcher] Dispatching: {task}")
    
    return {"current_task": task, "todo_list": todo}


def executor(state: GlobalState) -> dict:
    """
    Executor Node (LLM-driven with dynamic skill loading)
    
    Role: Execute current_task via the matching Skill; report result in last_result.
    Rule: @require_robot_active must guard all R-skills; halt_flag checked here.
    
    TODO: Replace stub with dynamic Skill loader + SkiLib.base.SkillResult integration.
    """
    task = state.get("current_task", {})
    
    if not task:
        return {
            "execution_log": ["[executor] No task — skipping."],
            "last_result": {"success": True},
        }
    
    if state.get("halt_flag"):
        return {
            "execution_log": [f"[executor] HALTED — skipping {task.get('task_id')}"],
            "last_result": {"success": False, "error_type": "ROBOT_INACTIVE"},
        }
    
    print(f"[executor] Running: {task['skill']}({task['params']})")
    
    # STUB: simulate success without touching robot
    return {
        "execution_log": [f"[executor] {task['task_id']} {task['skill']} -> SUCCESS (stub)"],
        "last_result": {"success": True},
    }


def context_flush(state: GlobalState) -> dict:
    """
    Context Flush Node (Pure Code, No LLM)
    
    Role: On success — clear current_task (empty the slot) and clear last_result.
          On failure — set halt_flag; current_task and todo_list are left intact so
                       the same task will be retried after human intervention resumes the system.
    
    TODO: Add RemoveMessage sweep once Executor uses real LangGraph tool calls.
    """
    task_id = state.get("current_task", {}).get("task_id", "?")
    last_result = state.get("last_result") or {}

    if last_result.get("success"):
        print(f"[context_flush] {task_id} SUCCESS — clearing slot.")
        return {
            "current_task": {},           # Empty the slot
            "last_result": None,          # Clear result data
            "execution_log": [f"[context_flush] {task_id} → slot cleared"]
        }
    else:
        error_type = last_result.get("error_type", "UNKNOWN")
        print(f"[context_flush] {task_id} FAILED ({error_type}) — setting halt_flag, retaining task.")
        return {
            "halt_flag": True,            # Trigger HILP
            "execution_log": [f"[context_flush] {task_id} → HALTED ({error_type})"]
            # current_task and todo_list are NOT modified — task will be retried on resume
        }


# ══════════════════════════════════════════════════════════════════════════════
# Graph Construction
# ══════════════════════════════════════════════════════════════════════════════

def should_continue(state: GlobalState) -> str:
    """
    Routing condition after context_flush.
    
    Priority (high to low):
    1. halt_flag=True      → "halt" → END (HILP triggered; await human intervention)
    2. todo_list non-empty → "continue" → dispatcher (fetch next task)
    3. Otherwise           → "done" → END (queue empty, normal completion)
    """
    if state.get("halt_flag"):
        return "halt"
    if state.get("todo_list"):
        return "continue"
    return "done"


def create_graph():
    """
    Construct and compile the LangGraph StateGraph.
    
    Returns:
        Compiled LangGraph application ready for invocation or LangGraph Studio.
    
    Flow:
        START → supervisor → planner → [dispatcher → executor → context_flush] × N → END
    
    Conditional routing after context_flush:
    - halt_flag=True      → halt → END
    - todo_list non-empty → continue → dispatcher
    - otherwise           → done → END
    """
    builder = StateGraph(GlobalState)

    # Register nodes
    builder.add_node("supervisor",    supervisor)
    builder.add_node("planner",       planner)
    builder.add_node("dispatcher",    dispatcher)
    builder.add_node("executor",      executor)
    builder.add_node("context_flush", context_flush)

    # Layer-1: linear planning flow
    builder.add_edge(START,        "supervisor")
    builder.add_edge("supervisor", "planner")
    builder.add_edge("planner",    "dispatcher")

    # Layer-2: execution loop
    builder.add_edge("dispatcher",   "executor")
    builder.add_edge("executor",     "context_flush")
    builder.add_conditional_edges(
        "context_flush",
        should_continue,
        {
            "continue": "dispatcher",  # slot cleared — fetch next task
            "done":     END,           # queue empty — all done
            "halt":     END,           # halt_flag set — await human intervention
        },
    )

    # Compile and return
    graph = builder.compile()
    print("[graph] LangGraph compiled successfully.")
    return graph


# ══════════════════════════════════════════════════════════════════════════════
# Standalone Test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 80)
    print("LangGraph Standalone Test")
    print("=" * 80)
    
    app = create_graph()
    
    # Visualize graph structure
    try:
        print("\n[graph] Mermaid diagram:\n")
        print(app.get_graph().draw_mermaid())
    except Exception as e:
        print(f"[graph] Visualization unavailable: {e}")
    
    # Test invocation
    initial_state: GlobalState = {
        "messages":      [HumanMessage(content="将 Part_A 放入 Tray_1")],
        "todo_list":     [],
        "current_task":  {},
        "robot_state":   {"joints": None, "pose": None, "gripper_state": "UNKNOWN"},
        "halt_flag":     False,
        "last_result":   None,
        "execution_log": [],
    }
    
    print("\n" + "=" * 80)
    print("Running workflow...")
    print("=" * 80 + "\n")
    
    final_state = app.invoke(initial_state)
    
    print("\n" + "=" * 80)
    print("Final State:")
    print("=" * 80)
    print(f"halt_flag:      {final_state.get('halt_flag')}")
    print(f"current_task:   {final_state.get('current_task')}")
    print(f"todo_list:      {final_state.get('todo_list')}")
    print(f"execution_log:  {final_state.get('execution_log')}")
