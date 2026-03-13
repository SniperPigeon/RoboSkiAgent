import functools
from abc import abstractmethod, ABC
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Callable, List, Dict, Any, Literal, Optional
from robodk import robolink, robomath


# ================ ExecutionPhase ================

class ExecutionPhase(Enum):
    """
    Identifies which phase a failure occurred in, so the LLM can choose the right recovery strategy.
    Granularity maps to three LLM decision branches — does not track individual action types.

    VALIDATION : Bad call arguments → LLM should fix parameters and retry
    PLANNING   : Path/IK/collision constraints unsatisfied → LLM should try a different target/path or request human intervention
    EXECUTION  : Robot runtime fault → LLM should check hardware, retry, or request human intervention

    The specific failure reason (IK vs collision vs timeout) is carried by SkillResult.error_type.
    Phase only conveys "at which stage did it fail".
    Adding new Primitives or Skills never requires modifying this enum.
    """
    VALIDATION = "VALIDATION"
    PLANNING   = "PLANNING"
    EXECUTION  = "EXECUTION"


# ================ RobotState ================

@dataclass
class RobotState:
    """
    Snapshot of the current robot state. All fields are Optional because state
    may be unavailable when the robot is unreachable.

    Attributes:
        joints:        Current joint angles in degrees.
        pose:          Current TCP pose. Callers may store any convenient format
                       (e.g. robomath.Mat, List[float]) — base.py stays platform-agnostic
                       by using Any rather than importing platform-specific types.
        gripper_state: Gripper state — "OPEN" / "CLOSED" / "UNKNOWN".
    """
    joints:        Optional[List[float]] = None
    pose:          Optional[Any]         = None
    gripper_state: Optional[str]         = None


# ================ error_type string constants ================
# String constants rather than an enum: new Primitives can define their own
# domain-specific constants in their own modules without touching this file.

ERROR_INVALID_PARAM     = "INVALID_PARAM"       # argument type or value is illegal
ERROR_MISSING_REF_FRAME = "MISSING_REF_FRAME"   # reference frame not provided
ERROR_IK_FAILURE        = "IK_FAILURE"          # inverse kinematics has no solution
ERROR_COLLISION         = "COLLISION"           # collision detected
ERROR_ROBOT_INACTIVE    = "ROBOT_INACTIVE"      # halt_flag=True, robot is locked
ERROR_TIMEOUT           = "TIMEOUT"             # action timed out


# ================ SkillResult ================

@dataclass
class SkillResult:
    """
    Unified return type for all public methods on Primitives and Skills
    (check / execute / try_execute).

    Goal: eliminate any possibility of raw Python tracebacks reaching the LLM.
    All low-level errors must be caught inside the Primitive layer and wrapped
    in a SkillResult before propagating upward.

    Attributes:
        success:         Whether the operation succeeded.
        execution_phase: Phase at which failure occurred (see ExecutionPhase);
                         on success, records the phase that was actually executed.
        robot_state:     Robot state snapshot at the end of the operation;
                         None when the robot is unreachable.
        message:         Human/LLM-readable description — concise and informative.
        error_type:      Failure category using ERROR_* constants; None on success.
                         Required when success=False, enforced by __post_init__.
        suggestion:      Recovery hint for the LLM or a human operator; may be None.
        data:            Payload returned on success, e.g. {"joints": [...]}.

    Usage:
        # Success
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.EXECUTION,
            robot_state=state,
            data={"joints": state.joints},
        )

        # Failure
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.VALIDATION,
            error_type=ERROR_MISSING_REF_FRAME,
            message="A pose target requires a reference frame.",
            suggestion="Pass a ref_frame argument when using pose targets.",
        )
    """

    success:         bool
    execution_phase: ExecutionPhase
    robot_state:     Optional[RobotState] = None
    message:         str                  = ""
    error_type:      Optional[str]        = None
    suggestion:      Optional[str]        = None
    data:            Optional[dict]       = None
    needs_hilp:      bool                 = True
    # needs_hilp semantics:
    #   True  (default) — Executor has given up; Context Flush should trigger HILP.
    #   False           — Executor's internal ReAct loop is still recovering; this state
    #                     must NOT be written to last_result when exiting the Executor node.
    #                     If Context Flush ever sees success=False + needs_hilp=False it
    #                     treats it conservatively as needs_hilp=True (safety net).

    def __post_init__(self):
        # Programming-time invariant: a failed result must carry error_type so the
        # LLM can categorize the failure. This is a developer error (same nature as
        # MissingPrimitiveError) — raising ValueError here is an intentional fast-fail.
        if not self.success and self.error_type is None:
            raise ValueError(
                "SkillResult: error_type must not be None when success=False. "
                "Assign one of the ERROR_* constants."
            )

    def to_llm_message(self) -> Dict[str, Any]:
        """
        Serialize to a structured dict for LLM consumption.
        Replaces CheckResult.toPlainText() and toStructuredMessage().
        Never exposes Python tracebacks, type names, or internal variable names.
        """
        payload: Dict[str, Any] = {
            "success": self.success,
            "phase":   self.execution_phase.value,
            "message": self.message,
        }
        if self.robot_state is not None:
            payload["robot_state"] = {
                "joints":        self.robot_state.joints,
                "pose":          self.robot_state.pose,
                "gripper_state": self.robot_state.gripper_state,
            }
        if not self.success:
            payload["error_type"] = self.error_type
            payload["needs_hilp"] = self.needs_hilp
            if self.suggestion:
                payload["suggestion"] = self.suggestion
        if self.data:
            payload["data"] = self.data
        return payload


# ================ CheckResult (kept for migration period) ================

class CheckResultLevel:
    ERROR   = "error"
    WARNING = "warning"
    INFO    = "info"


@dataclass
class CheckResult:
    """
    Legacy return type for check() methods; retained during the migration period.
    New code must use SkillResult exclusively — do not add new uses of CheckResult.

    To migrate: call .to_skill_result(phase) to convert to a SkillResult.
    """

    is_valid:   bool
    message:    str                                  = ""
    category:   Optional[str]                        = None
    level:      Literal["error", "warning", "info"]  = CheckResultLevel.INFO
    details:    Optional[Dict[str, Any]]              = None
    suggestion: Optional[str]                         = None

    def toPlainText(self) -> str:
        result = ""
        if self.level:
            result += f"[{self.level}]\t"
        if self.category:
            result += f"{self.category}: "
        result += "Check PASSED.\n" if self.is_valid else "Check FAILED.\n"
        if self.details:
            result += f"\tCheck details: {self.details}\n"
        if self.suggestion:
            result += f"\tSuggestion for fixing: {self.suggestion}\n"
        return result

    def toStructuredMessage(self) -> Dict[str, Any]:
        return asdict(self)

    def to_skill_result(self, phase: ExecutionPhase) -> SkillResult:
        """
        Migration bridge: convert a CheckResult to a SkillResult.

        Field mapping:
            is_valid   → success
            category   → error_type (on failure; falls back to ERROR_INVALID_PARAM if None)
            message    → message
            suggestion → suggestion
            details    → data
            level      → not mapped (severity is now conveyed by ExecutionPhase + error_type)
        """
        if self.is_valid:
            return SkillResult(
                success=True,
                execution_phase=phase,
                message=self.message,
                data=self.details,
            )
        return SkillResult(
            success=False,
            execution_phase=phase,
            error_type=self.category or ERROR_INVALID_PARAM,
            message=self.message,
            suggestion=self.suggestion,
            data=self.details,
        )


# ================ Exceptions ================

class MissingPrimitiveError(Exception):
    """
    Raised when a Skill is initialised without a required Primitive.
    This is a deployment/configuration error (programming time), not a runtime
    failure — it should crash fast and must not be handled by the LLM.
    """
    pass


# ================ BasePrimitive ================

class BasePrimitive(ABC):
    """
    Base class for all robot primitives.

    Primitives are platform-specific low-level implementations (e.g. RoboDK)
    that are automatically instantiated and managed by PrimitiveRegistry.

    Implementation requirements:
        1. Import platform-specific libraries (robodk, etc.) inside the module.
        2. Implement check() / execute() / try_execute(), all returning SkillResult.
        3. execute() must catch all exceptions internally — never let them propagate.
        4. Place the module inside the primitives/ directory for auto-discovery.
    """

    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item     = robot_object
        self.RDK:   robolink.Robolink = RDK_object

    @abstractmethod
    def check(self, *args, **kwargs) -> SkillResult:
        """Pre-flight validation. Phase should be VALIDATION or PLANNING."""
        pass

    @abstractmethod
    def execute(self, *args, **kwargs) -> SkillResult:
        """Execute the primitive action. Must catch all exceptions internally."""
        pass

    @abstractmethod
    def try_execute(self, *args, **kwargs) -> SkillResult:
        """Run check(), then execute() if the check passed. Returns a single SkillResult."""
        pass


# ================ BaseSkill ================

class BaseSkill(ABC):
    """
    Base class for high-level robot skills.

    Skills are platform-agnostic and composed from Primitives.
    They must not import robodk or other platform-specific libraries.

    Subclasses declare dependencies via REQUIRED_PRIMITIVES; the full primitive
    registry is passed at init and missing dependencies raise immediately.

    Usage:
        class PickAndPlace(BaseSkill):
            REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL']

            def execute(self, target) -> SkillResult:
                return self.primitives['MoveJ'].execute(target)

        skill = PickAndPlace(context.primitives)
    """

    REQUIRED_PRIMITIVES: List[str] = []

    def __init__(self, primitives: Dict[str, 'BasePrimitive']):
        """
        Args:
            primitives: Full primitive registry dict, e.g. context.primitives.

        Raises:
            MissingPrimitiveError: If any key in REQUIRED_PRIMITIVES is absent.
        """
        missing = [name for name in self.REQUIRED_PRIMITIVES if name not in primitives]
        if missing:
            raise MissingPrimitiveError(
                f"{self.__class__.__name__} is missing required primitive(s): {missing}. "
                f"Available: {list(primitives.keys())}"
            )
        self.primitives = primitives

    @abstractmethod
    def check(self, *args, **kwargs) -> SkillResult:
        """Pre-flight validation."""
        pass

    @abstractmethod
    def execute(self, *args, **kwargs) -> SkillResult:
        """Execute the skill. Must catch all exceptions internally."""
        pass

    @abstractmethod
    def try_execute(self, *args, **kwargs) -> SkillResult:
        """Run check(), then execute() if the check passed. Returns a single SkillResult."""
        pass


# ================ @require_robot_active decorator ================

def require_robot_active(_func: Optional[Callable] = None, *, bypass_halt: bool = False):
    """
    Guard decorator for Primitive/Skill execute() methods.

    Checks RobotContext.halt_flag at the entry of each decorated call.
    When halt_flag is True the call is short-circuited and a SkillResult
    with error_type=ERROR_ROBOT_INACTIVE is returned immediately, without
    touching the robot hardware.

    Usage:
        # Standard – block when halted
        @require_robot_active
        def execute(self, ...) -> SkillResult: ...

        # Whitelist – must bypass halt to avoid permanent deadlock
        @require_robot_active(bypass_halt=True)
        def resume(self) -> SkillResult: ...

    Whitelist (bypass_halt=True required):
        - resume()
        - request_human_intervention()

    Notes:
        - The decorator uses a lazy import of RobotContext to avoid circular
          imports between base.py and robotcontext.py.
        - If RobotContext has not been initialised yet (e.g. unit tests without
          a RoboDK connection), the check is skipped and the wrapped function
          runs normally.
        - halt_flag is read from RobotContext; the Executor node is responsible
          for synchronising GlobalState["halt_flag"] → RobotContext.halt_flag
          before invoking any skill.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> SkillResult:
            if not bypass_halt:
                # Lazy import avoids circular dependency: base ← robotcontext ← base
                from SkiLib.robotcontext import RobotContext  # noqa: PLC0415
                ctx = RobotContext.instance()
                if ctx is not None and ctx.halt_flag:
                    return SkillResult(
                        success=False,
                        execution_phase=ExecutionPhase.EXECUTION,
                        error_type=ERROR_ROBOT_INACTIVE,
                        message=(
                            "Robot is inactive: halt_flag is set. "
                            "No commands will be dispatched until the halt is cleared."
                        ),
                        suggestion="Call resume() or request_human_intervention() to clear the halt.",
                    )
            return func(*args, **kwargs)
        return wrapper

    # Support both @require_robot_active and @require_robot_active(bypass_halt=True)
    if _func is not None:
        # Called without parentheses: @require_robot_active
        return decorator(_func)
    # Called with parentheses: @require_robot_active(...)
    return decorator
