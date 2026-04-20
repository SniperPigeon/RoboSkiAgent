from SkiLib.verifiers.base import VerificationConfig, VerificationResult
from SkiLib.verifiers.rule_check import rule_check
from SkiLib.log import get_logger

logger = get_logger(__name__)


class TaskVerifier:
    """
    Entry point for SFT data collection.
    Verifies whether a completed task achieved its intended physical outcome.

    Usage:
        verifier = TaskVerifier()
        result = verifier.verify(VerificationConfig(
            task_instruction="Pick up Part_A and place it at Target_1",
            expected_items=[
                ItemExpectation(item_name="Part_A", near_target="Target_1", tolerance_mm=10.0),
            ]
        ))
        # result.success, result.reason, result.evidence → write to SFT dataset
    """

    def verify(self, config: VerificationConfig) -> VerificationResult:
        logger.info("Verifying task: %s", config.task_instruction)
        result = rule_check(config)
        logger.info("Verification result: success=%s | %s", result.success, result.reason)
        return result
