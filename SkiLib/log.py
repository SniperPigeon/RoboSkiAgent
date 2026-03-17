import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_LEVEL = os.environ.get("ROBOSKI_LOG_LEVEL", "INFO").upper()
_LOG_DIR   = Path(__file__).parent.parent / "logs"
_LOG_FILE  = _LOG_DIR / "roboski.log"
_FMT       = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"


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

    fmt = logging.Formatter(_FMT)

    # --- console handler ---
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # --- rotating file handler ---
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Prevent the root logger from printing the same record a second time.
    logger.propagate = False

    return logger
