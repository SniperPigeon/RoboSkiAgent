"""Gradio UI for RoboSkiAgent HITL demo.

This module is a leaf consumer: it imports build_graph but the graph/nodes
modules have zero Gradio imports.

Usage:
    python -m Agent.gui
    # or from code:
    from Agent.gui import launch_gui
    launch_gui(graph)
"""
# On macOS, pyglet (used by Genesis viewer) requires an NSApplication context that
# only exists when running via python.app.  Re-exec directly with the python.app
# binary (not the pythonw shell wrapper, which requires an extra fork).
import sys as _sys
import os as _os
# if _sys.platform == "darwin" and "python.app" not in _sys.executable:
#     from pathlib import Path as _Path
#     # python.app lives two levels up from the conda env's bin/python
#     _python_app = _Path(_sys.executable).parent.parent / "python.app/Contents/MacOS/python"
#     if _python_app.exists():
#         _os.environ.setdefault("PYTHONEXECUTABLE", _sys.executable)
#         _os.execv(str(_python_app), [str(_python_app)] + _sys.argv)

# If window cannot hold on macos, decomment this to force cocoa
# TODO: macOS下打不开viewer，据查询可能是因为pyglet在pip上的版本无法正确处理OpenGL3+，后面先去linux环境试试
import platform, os
if platform.system() == "Darwin":
    # Force Cocoa app activation so pyglet window survives
    os.environ.setdefault("PYOBJUS_MACOS_APPKIT_THREAD_CHECK", "0")
    
import logging
import os
import queue as _queue_module
import threading
import uuid

import gradio as gr
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from SkiLib.genesis.controller import GenesisController
from SkiLib.log import get_logger, attach_queue_handler
from SkiLib.sim_env import setup_robot_env
# ── Version switch ─────────────────────────────────────────────────────────
# Set USE_V2 = True to run the skill.md-based planner/executor (V2 graph).
# Set USE_V2 = False to run the original Python BaseSkill graph (V1).
USE_V2: bool = True

if USE_V2:
    from Agent.graph_v2 import build_graph_v2 as build_graph
else:
    from Agent.graph import build_graph
from Agent.graph import make_initial_state   # shared; same in both versions
from Agent.nodes.supervisor import reset_supervisor_cache


def _setup_env() -> None:
    """Load .env and auto-enable LangSmith tracing if API key is present."""
    load_dotenv(override=True)
    if os.getenv("LANGSMITH_API_KEY") and not os.getenv("LANGSMITH_TRACING"):
        os.environ["LANGSMITH_TRACING"] = "true"
    if os.getenv("LANGSMITH_TRACING", "false").lower() == "true":
        logger.info("[env] LangSmith tracing enabled (project: %s)",
                    os.getenv("LANGSMITH_PROJECT", "(default)"))

logger = get_logger(__name__)

MAX_BUTTONS = 4

_gui_formatter = logging.Formatter("[%(levelname)s] %(name)s — %(message)s")


def _format_record(record: logging.LogRecord) -> str:
    return _gui_formatter.format(record)


def _drain_queue(q: _queue_module.Queue):
    """Discard stale log records left over from a previous run."""
    while not q.empty():
        try:
            q.get_nowait()
        except Exception:
            break


def _collect_queue(q: _queue_module.Queue, log_lines: list[str]) -> bool:
    """Drain all currently available records into log_lines. Returns True if any added."""
    added = False
    while not q.empty():
        try:
            record = q.get_nowait()
            log_lines.append(_format_record(record))
            added = True
        except Exception:
            break
    return added


# ---- UI helpers ---------------------------------------------------------------
def _get_button_updates(options):
    updates = []
    for i in range(MAX_BUTTONS):
        if i < len(options):
            updates.append(gr.update(value=options[i], visible=True))
        else:
            updates.append(gr.update(visible=False))
    return updates


def _hide_buttons():
    return [gr.update(visible=False)] * MAX_BUTTONS


# ---- Interrupt helper ---------------------------------------------------------
def _check_for_interrupt(graph, config: dict, session: dict, log_lines: list[str]):
    """Check graph state for an active interrupt and build the Gradio output tuple."""
    state    = graph.get_state(config)
    log_text = "\n".join(log_lines)

    has_interrupt = (
        state.next
        and state.tasks
        and state.tasks[0].interrupts
    )
    if has_interrupt:
        interrupt_val = state.tasks[0].interrupts[0].value
        options      = interrupt_val.get("options", [])
        description  = interrupt_val.get("description", "")
        session["waiting"] = True
        log_text += f"\n[ Task ] {description}\n⏸ Waiting for human input..."
        return [log_text, session] + _get_button_updates(options)

    session["waiting"] = False
    # Graph finished normally (no pending interrupt) — append completion notice
    final_state = graph.get_state(config).values
    todo_remaining = final_state.get("todo_list", [])
    halt = final_state.get("halt_flag", False)
    if halt:
        log_text += "\n\n⚠️  Process halted due to failure (halt_flag=True)"
    elif todo_remaining:
        log_text += f"\n\n⚠️  Process ended with {len(todo_remaining)} task(s) remaining"
    else:
        log_text += "\n\n✅  Process completed. All tasks done."
    return [log_text, session] + _hide_buttons()


# ---- Public entry point -------------------------------------------------------
def launch_gui(
    graph=None,
    log_queue: _queue_module.Queue | None = None,
    debug_skip_check: bool | None = None,
    **kwargs,
):
    """Build and launch the Gradio demo.

    Args:
        graph:            Pre-compiled graph. Created via build_graph() if None.
        log_queue:        Queue to attach for live log streaming. Created internally if None.
        debug_skip_check: Pass True to skip IK/collision checks (simulation mode).
        **kwargs:         Forwarded to demo.launch().
    """
    _setup_env()

    if debug_skip_check is None:
        debug_skip_check = os.getenv("ROBOSKI_SKIP_CHECK", "true").lower() in ("1", "true", "yes")

    if log_queue is None:
        log_queue = _queue_module.Queue()
        attach_queue_handler(log_queue)

    ctx = setup_robot_env(debug_skip_check=debug_skip_check)

    # Attach a GenesisController so all scene.step() calls are serialised onto
    # one thread.  On macOS the pyrender viewer cannot run in a background thread,
    # so we run Gradio non-blocking and keep the main thread for Genesis.
    genesis_ctrl = GenesisController(ctx.runtime)
    ctx.runtime.controller = genesis_ctrl

    if graph is None:
        graph = build_graph()

    # ---- start_flow (streaming generator) ------------------------------------
    def start_flow(prompt, session):
        _drain_queue(log_queue)

        thread_id = str(uuid.uuid4())
        session["thread_id"] = thread_id
        config = {"configurable": {"thread_id": thread_id}}

        reset_supervisor_cache()

        initial_state = make_initial_state(prompt)

        done_event = threading.Event()

        def _run():
            try:
                graph.invoke(initial_state, config=config)
            finally:
                done_event.set()

        threading.Thread(target=_run, daemon=True).start()

        log_lines: list[str] = []
        while not done_event.is_set():
            if _collect_queue(log_queue, log_lines):
                yield ["\n".join(log_lines), session] + _hide_buttons()
            else:
                import time; time.sleep(0.05)

        _collect_queue(log_queue, log_lines)
        yield _check_for_interrupt(graph, config, session, log_lines)

    # ---- handle_choice (streaming generator) ---------------------------------
    def handle_choice(choice, feedback, session):
        if not session.get("waiting"):
            yield ["", session] + _hide_buttons()
            return

        _drain_queue(log_queue)

        config     = {"configurable": {"thread_id": session["thread_id"]}}
        done_event = threading.Event()

        payload = {"action": "replan", "feedback": (feedback or "").strip()} if choice == "replan" else choice

        def _run():
            try:
                graph.invoke(Command(resume=payload), config=config)
            finally:
                done_event.set()

        threading.Thread(target=_run, daemon=True).start()

        log_lines: list[str] = []
        while not done_event.is_set():
            if _collect_queue(log_queue, log_lines):
                yield ["\n".join(log_lines), session] + _hide_buttons()
            else:
                import time; time.sleep(0.05)

        _collect_queue(log_queue, log_lines)
        yield _check_for_interrupt(graph, config, session, log_lines)

    # ---- Gradio layout -------------------------------------------------------
    with gr.Blocks(title="RoboSkiAgent HITL Demo") as demo:
        session_state = gr.State({})

        log_box = gr.Textbox(
            label="Execution Log",
            lines=20,
            interactive=False,
            autoscroll=True,
        )

        with gr.Row():
            buttons = [gr.Button(visible=False) for _ in range(MAX_BUTTONS)]

        feedback_box = gr.Textbox(
            label="Feedback (fill in when choosing replan)",
            placeholder="e.g. Change place_target in task 3 to Place_B, keep the rest unchanged",
            lines=3,
        )

        with gr.Row():
            prompt_box = gr.Textbox(
                placeholder="Enter assembly instruction...",
                show_label=False,
                scale=4,
            )
            start_btn = gr.Button("Start", variant="primary", scale=1)

        all_outputs = [log_box, session_state] + buttons

        start_btn.click(fn=start_flow,   inputs=[prompt_box, session_state], outputs=all_outputs)
        prompt_box.submit(fn=start_flow, inputs=[prompt_box, session_state], outputs=all_outputs)

        for btn in buttons:
            btn.click(fn=handle_choice, inputs=[btn, feedback_box, session_state], outputs=all_outputs)

    # prevent_thread_lock=True lets Gradio start in background threads so the
    # main thread is free to run the Genesis controller loop below.
    kwargs.setdefault("prevent_thread_lock", True)
    demo.launch(**kwargs)

    try:
        genesis_ctrl.run()
    except KeyboardInterrupt:
        pass
    finally:
        genesis_ctrl.stop()


if __name__ == "__main__":
    launch_gui()
