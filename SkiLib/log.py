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
