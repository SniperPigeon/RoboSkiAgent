"""
sim_env.py — Simulation environment bootstrapping.

Genesis runtime bootstrap helpers.
"""
import os

from SkiLib.log import get_logger
from SkiLib.robotcontext import RobotContext

logger = get_logger(__name__)


def setup_robot_env(debug_skip_check: bool | None = None) -> RobotContext:
    """Initialize robot context and skill loader.

    Args:
        debug_skip_check: Skip IK/collision checks. None reads ROBOSKI_SKIP_CHECK env var.

    Returns:
        The initialized RobotContext singleton.
    """
    from SkiLib.skill_loader import SkillMdLoader

    ctx = RobotContext()
    if debug_skip_check is None:
        debug_skip_check = os.getenv("ROBOSKI_SKIP_CHECK", "false").lower() in ("1", "true", "yes")
    ctx.debug_skip_check = debug_skip_check
    SkillMdLoader.instance()
    logger.info("[sim_env] setup_robot_env complete. debug_skip_check=%s", debug_skip_check)
    return ctx


def reset_station() -> None:
    """Reset the Genesis simulation by rebuilding the RobotContext singleton."""
    RobotContext._instance = None
    RobotContext()
    logger.info("[sim_env] Genesis scene reset completed.")
