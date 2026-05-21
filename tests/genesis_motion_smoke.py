"""Smoke test for Genesis motion primitives.

Run:
    conda run -n rsagent python tests/genesis_motion_smoke.py
    # If your shell is already inside `(rsagent)`, launch the viewer directly:
    ROBOSKI_GENESIS_VIEWER=1 python tests/genesis_motion_smoke.py --viewer --hold-seconds 20
"""

import argparse
import os
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from SkiLib.robotcontext import RobotContext


def _must_succeed(label: str, result) -> None:
    payload = result.to_llm_message()
    print(f"{label}: {payload}")
    if not result.success:
        raise SystemExit(f"{label} failed: {payload}")


def _hold(ctx: RobotContext, seconds: float) -> None:
    if seconds <= 0:
        return
    deadline = time.time() + seconds
    print(f"Holding viewer for {seconds:.1f}s...")
    while time.time() < deadline:
        ctx.scene.step()


def main() -> None:
    parser = argparse.ArgumentParser(description="Genesis motion primitive smoke test.")
    parser.add_argument("--viewer", action="store_true", help="Open Genesis viewer while running the smoke test.")
    parser.add_argument("--hold-seconds", type=float, default=0.0, help="Keep stepping after the test so the final pose is visible.")
    parser.add_argument("--pause-between", type=float, default=0.0, help="Pause/step between motions for easier visual inspection.")
    args = parser.parse_args()

    if args.viewer:
        os.environ["ROBOSKI_GENESIS_VIEWER"] = "1"
        os.environ["ROBOSKI_GENESIS_BUILD_VISUALIZER"] = "1"

    try:
        ctx = RobotContext()
    except RuntimeError as e:
        if args.viewer and "OpenGL" in str(e):
            raise SystemExit(
                "Genesis viewer failed to initialize an OpenGL context.\n"
                "On macOS, do not launch viewer tests through `conda run` from an already activated env.\n"
                "Try instead:\n"
                "  ROBOSKI_GENESIS_VIEWER=1 python tests/genesis_motion_smoke.py --viewer --pause-between 2 --hold-seconds 20\n"
                "If that still fails, your conda Python may not be GUI-framework enabled; install/use python.app or run headless."
            ) from e
        raise
    movej = ctx.primitives["MoveJ"]
    movel = ctx.primitives["MoveL"]

    _must_succeed("MoveJ Home_position", movej.try_execute(ctx.resolve_target("Home_position")))
    _hold(ctx, args.pause_between)
    _must_succeed("MoveL PartA_Approach", movel.try_execute(ctx.resolve_target("PartA_Approach")))
    _hold(ctx, args.pause_between)
    _must_succeed("MoveL PartA_Pick", movel.try_execute(ctx.resolve_target("PartA_Pick")))
    _hold(ctx, args.pause_between)

    state = ctx.get_current_state()
    print("Final TCP pose:", state.pose)
    print("Final gripper state:", state.gripper_state)
    _hold(ctx, args.hold_seconds)


if __name__ == "__main__":
    main()
