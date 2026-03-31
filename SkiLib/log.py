import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import queue

_LOG_LEVEL = os.environ.get("ROBOSKI_LOG_LEVEL", "INFO").upper()
_LOG_DIR   = Path(__file__).parent.parent / "logs"
_LOG_FILE  = _LOG_DIR / "roboski.log"
_FMT       = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"

# Registry of all loggers created by get_logger(), used by attach_queue_handler()
_registered_loggers: list[logging.Logger] = []
_active_queue_handlers: list[logging.Handler] = []

# ANSI color codes keyed by log level
_ANSI_COLORS = {
    "DEBUG":    "\033[90m",   # dark grey
    "INFO":     "\033[97m",   # bright white
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[41;97m", # red bg + white text
}
_ANSI_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """StreamHandler-only formatter that prepends ANSI color codes by level."""

    def format(self, record: logging.LogRecord) -> str:
        color = _ANSI_COLORS.get(record.levelname, "")
        return color + super().format(record) + _ANSI_RESET


def get_logger(name: str) -> logging.Logger:
    """
    Return a module-level logger with two handlers:
      - StreamHandler: console output (mirrors print behaviour)
      - RotatingFileHandler: writes to logs/roboski.log (10 MB × 5 backups)

    Log level is controlled by the ROBOSKI_LOG_LEVEL environment variable
    (default: INFO).  Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL.

    Usage (at module top-level)::

        from SkiLib.log import get_logger
        logger = get_logger(__name__)
    """
    logger = logging.getLogger(name)

    # Guard: avoid adding duplicate handlers when the same module is imported
    # multiple times or when get_logger() is called more than once.
    if logger.handlers:
        return logger

    logger.setLevel(_LOG_LEVEL)

    plain_fmt = logging.Formatter(_FMT)
    color_fmt = _ColorFormatter(_FMT)

    # --- console handler (colored) ---
    sh = logging.StreamHandler()
    sh.setFormatter(color_fmt)
    logger.addHandler(sh)

    # --- rotating file handler ---
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(plain_fmt)
    logger.addHandler(fh)

    # Prevent the root logger from printing the same record a second time.
    logger.propagate = False

    # Attach any queue handlers that were registered before this logger was created.
    for handler in _active_queue_handlers:
        logger.addHandler(handler)

    _registered_loggers.append(logger)
    return logger


def attach_queue_handler(q: "queue.Queue", level: str = "INFO") -> None:
    """Attach a QueueHandler to all existing and future loggers from get_logger().

    Call this once at application startup (e.g. from the Agent layer or a
    Gradio notebook) to enable real-time log streaming without introducing
    any LangGraph dependency into SkiLib.

    Args:
        q:     A ``queue.Queue`` instance consumed by the UI layer.
        level: Minimum log level forwarded to the queue (default: INFO).

    Usage::

        import queue
        from SkiLib.log import attach_queue_handler

        log_queue = queue.Queue()
        attach_queue_handler(log_queue)
        # Now poll log_queue in your UI to get real-time log lines.
    """
    from logging.handlers import QueueHandler

    handler = QueueHandler(q)
    handler.setLevel(level)
    _active_queue_handlers.append(handler)
    for logger in _registered_loggers:
        # Guard against adding duplicate handlers on repeated calls.
        if not any(isinstance(h, QueueHandler) for h in logger.handlers):
            logger.addHandler(handler)

"""
Centralized logging configuration for SkiLib.

All production code in SkiLib/ must use get_logger(__name__) instead of print().
Provides a dual-handler setup: StreamHandler (console) + RotatingFileHandler (file).
"""

import logging
import logging.handlers
import pathlib

_LOG_DIR  = pathlib.Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "skilib.log"
_MAX_BYTES    = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3
_FORMAT      = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_root_initialized = False


def _init_root_logger() -> None:
    """Initialize the 'SkiLib' root logger once. Idempotent."""
    global _root_initialized
    if _root_initialized:
        return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger("SkiLib")
    root.setLevel(logging.DEBUG)
    # Guard against double-adding handlers if the module is reloaded
    if not root.handlers:
        root.addHandler(console_handler)
        root.addHandler(file_handler)

    _root_initialized = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a module-level logger.  Call once at module import time:

        logger = get_logger(__name__)

    The SkiLib root logger (console + rotating file) is initialized automatically
    on the first call and reused on all subsequent calls.
    """
    _init_root_logger()
    return logging.getLogger(name)
