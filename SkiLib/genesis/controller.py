"""
GenesisController — serialises all Genesis physics calls onto a single thread.

On macOS the pyrender viewer cannot run in a background thread (Genesis enforces
this at init time).  ``scene.step()`` must therefore be called from the thread
that owns the OpenGL context — typically the process main thread.

Architecture
------------
::

    Main thread                        Worker thread (LangGraph / Agent)
    ─────────────────────────────      ─────────────────────────────────────────
    GenesisController.run()   ◄──────  primitive.execute()
      idle: hold + step               └─ _submit_to_controller(_execute_body, ...)
      task: fn(*args) + step            └─ ctrl.submit(fn, ...) → Future.result()
                                                 ▲ blocks until done

Usage (in gui.py)
-----------------
::

    ctx = setup_robot_env()
    ctrl = GenesisController(ctx.runtime)
    ctx.runtime.controller = ctrl

    demo.launch(prevent_thread_lock=True, ...)   # Gradio in background
    ctrl.run()                                    # main thread owns Genesis/viewer
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable

import numpy as np


class GenesisController:
    """Task queue that serialises all ``scene.step()`` calls onto one thread."""

    # Timeout for the idle queue poll (seconds).  Controls how quickly the
    # viewer refreshes when no agent work is pending (~60 fps at 0.016 s).
    _IDLE_POLL_S = 0.016

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self._queue: queue.Queue = queue.Queue()
        self._stopped = threading.Event()
        self._thread_id: int | None = None

    # ------------------------------------------------------------------
    # API for worker threads
    # ------------------------------------------------------------------

    def submit(self, fn: Callable, /, *args: Any, **kwargs: Any) -> Any:
        """Submit *fn* to the Genesis thread and block the caller until it returns.

        Thread-safe.  May be called from any thread except the Genesis thread
        itself (that would deadlock).
        """
        if self.is_genesis_thread():
            # Already on the Genesis thread — run directly to avoid deadlock.
            return fn(*args, **kwargs)
        fut: Future = Future()
        self._queue.put((fn, args, kwargs, fut))
        return fut.result()

    def is_genesis_thread(self) -> bool:
        """Return True when called from the thread that owns the Genesis scene."""
        return self._thread_id is not None and threading.get_ident() == self._thread_id

    # ------------------------------------------------------------------
    # Main loop — must be called from the Genesis-owning thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Drive Genesis physics and viewer.

        **Must** be called from the thread that built the scene (the process
        main thread on macOS).  Blocks until :meth:`stop` is called.
        """
        self._thread_id = threading.get_ident()
        arm_dofs = self.runtime.bundle.arm_dofs

        while not self._stopped.is_set():
            try:
                fn, args, kwargs, fut = self._queue.get(timeout=self._IDLE_POLL_S)
                try:
                    result = fn(*args, **kwargs)
                    fut.set_result(result)
                except Exception as exc:
                    fut.set_exception(exc)
            except queue.Empty:
                # No pending work — hold robot position to keep the viewer alive.
                try:
                    cur = np.asarray(self.runtime.robot.get_qpos().tolist(), dtype=float)
                    self.runtime.robot.control_dofs_position(
                        cur[arm_dofs], dofs_idx_local=arm_dofs
                    )
                    self.runtime.scene.step()
                except Exception:
                    pass

    def stop(self) -> None:
        """Signal :meth:`run` to return after finishing the current task."""
        self._stopped.set()
