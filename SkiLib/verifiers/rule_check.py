from __future__ import annotations
import math

from SkiLib.verifiers.base import VerificationConfig, ItemExpectation, VerificationResult
from SkiLib.log import get_logger

logger = get_logger(__name__)


def rule_check(config: VerificationConfig) -> VerificationResult:
    """
    Query RoboDK state and verify all ItemExpectations in config.
    Returns VerificationResult — always conclusive (no LLM fallback here).
    """
    from SkiLib.robotcontext import RobotContext

    ctx = RobotContext.instance()
    if ctx is None:
        return VerificationResult(
            success=False,
            reason="RobotContext not initialized — cannot query RoboDK state",
            evidence={},
        )

    evidence: dict = {}

    for exp in config.expected_items:
        item = ctx.RDK.Item(exp.item_name)
        if not item.Valid():
            return VerificationResult(
                success=False,
                reason=f"Item '{exp.item_name}' not found in RoboDK scene",
                evidence=evidence,
            )

        # Check 1: item must not still be held by gripper
        if exp.detached_from_gripper:
            gripper_state = ctx.get_gripper_state()
            grasped: list[str] = gripper_state.get("grasped", [])
            if exp.item_name in grasped:
                evidence[exp.item_name] = {"grasped_by": gripper_state.get("active_tool")}
                return VerificationResult(
                    success=False,
                    reason=f"'{exp.item_name}' is still grasped by gripper '{gripper_state.get('active_tool')}'",
                    evidence=evidence,
                )

        # Check 2: item must be within tolerance of near_target
        if exp.near_target is not None:
            target = ctx.RDK.Item(exp.near_target)
            if not target.Valid():
                return VerificationResult(
                    success=False,
                    reason=f"Target '{exp.near_target}' not found in RoboDK scene",
                    evidence=evidence,
                )

            item_pos = item.PoseAbs().Pos()
            target_pos = target.PoseAbs().Pos()
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(item_pos, target_pos)))

            evidence[exp.item_name] = {
                "dist_mm": round(dist, 2),
                "near_target": exp.near_target,
                "tolerance_mm": exp.tolerance_mm,
            }
            logger.debug("'%s' → '%s': dist=%.1f mm (tol=%.1f mm)",
                         exp.item_name, exp.near_target, dist, exp.tolerance_mm)

            if dist > exp.tolerance_mm:
                return VerificationResult(
                    success=False,
                    reason=(
                        f"'{exp.item_name}' is {dist:.1f} mm from '{exp.near_target}' "
                        f"(tolerance={exp.tolerance_mm} mm)"
                    ),
                    evidence=evidence,
                )

    return VerificationResult(
        success=True,
        reason="All items verified at expected positions",
        evidence=evidence,
    )
