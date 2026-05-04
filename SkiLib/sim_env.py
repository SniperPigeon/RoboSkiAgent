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
    """Reset the Genesis simulation to home state for repeated experiments.

    Submits GenesisRuntime.reset() to the Genesis thread (via controller if
    present) so scene.step() serialisation is respected.  After the reset,
    GenesisController.run() automatically updates its hold_qpos from the
    post-reset robot position (home_qpos), so the arm stays at home.
    """
    ctx = RobotContext.instance()
    if ctx is None:
        logger.warning("[sim_env] reset_station: RobotContext not initialised, skipping.")
        return
    runtime = ctx.runtime
    ctrl = getattr(runtime, "controller", None)
    if ctrl is not None:
        ctrl.submit(runtime.reset)
    else:
        runtime.reset()
    logger.info("[sim_env] Genesis scene reset to home state.")
