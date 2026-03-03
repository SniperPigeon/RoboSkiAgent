from abc import abstractmethod
from dataclasses import dataclass, field
from abc import ABC
from typing import List, Dict, Any, Literal, Optional
from robodk import robolink, robomath
from dataclasses import asdict
# ================ BaseSkill ================

class BasePrimitive(ABC):
    """
    Base class for all robot primitives.
    
    Primitives are low-level, platform-specific implementations (e.g., RoboDK).
    They are automatically instantiated and managed by PrimitiveRegistry.
    
    Implementers should:
        1. Import platform-specific libraries (robodk, etc.) in their module
        2. Implement check(), execute(), try_execute()
        3. Place in primitives/ folder for auto-discovery
    """
    
    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item = robot_object
        self.RDK: robolink.Robolink = RDK_object
    
    @abstractmethod
    def check(self, *args, **kwargs) -> 'CheckResult':
        """Check if primitive can execute with given parameters"""
        pass
    
    @abstractmethod
    def execute(self, *args, **kwargs):
        """Execute the primitive action"""
        pass
    
    @abstractmethod
    def try_execute(self, *args, **kwargs):
        """Try to execute (check first, then execute if valid)"""
        pass


class MissingPrimitiveError(Exception):
    """Raised when a skill is initialized without a required primitive."""
    pass


class BaseSkill(ABC):
    """
    Base class for high-level robot skills.

    Skills are platform-agnostic and composed from primitives.
    They should NOT import robodk or other platform-specific libraries.

    Subclasses declare their dependencies via REQUIRED_PRIMITIVES.
    The entire primitive registry is passed in at init - the skill
    picks what it needs, and missing required primitives raise immediately.

    Usage:
        class PickAndPlace(BaseSkill):
            REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL']

            def execute(self, target):
                self.primitives['MoveJ'].execute(target)

        # At runtime:
        skill = PickAndPlace(context.primitives)   # pass full registry dict
    """


    # Subclasses override this to declare their mandatory primitives
    REQUIRED_PRIMITIVES: List[str] = []

    def __init__(self, primitives: Dict[str, 'BasePrimitive']):
        """
        Args:
            primitives: Full primitive registry dict, e.g. context.primitives

        Raises:
            MissingPrimitiveError: If any REQUIRED_PRIMITIVES key is absent
        """
        missing = [name for name in self.REQUIRED_PRIMITIVES if name not in primitives]
        if missing:
            raise MissingPrimitiveError(
                f"{self.__class__.__name__} is missing required primitive(s): {missing}. "
                f"Available: {list(primitives.keys())}"
            )
        self.primitives = primitives

    @abstractmethod
    def check(self, *args, **kwargs) -> 'CheckResult':
        """Check if skill can execute with given parameters"""
        pass

    @abstractmethod
    def execute(self, *args, **kwargs):
        """Execute the skill"""
        pass

    @abstractmethod
    def try_execute(self, *args, **kwargs):
        """Try to execute (check first, then execute if valid)"""
        pass
    
    
    
# ================ CheckResult ================
class CheckResultLevel:
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
@dataclass
class CheckResult:
    """
    CheckResult encapsulates the result of skill/primtive constraint checks result.
    This structured message can be used by human or LLM to understand the reason of failure and generate improvement plan. 
    The structured format also allows programmatic parsing for automatic improvement or debugging.
    
    Attributes:
        is_valid: is check passed or not.
        message: Description of the check result, can be used for both human and LLM understanding. Should be concise and informative.
        category: Type of the check result, e.g. "collision", "singularity", "joint_limit", etc. 
        This can help LLM to categorize the failure and generate specific improvement plan.
        level: Severity level of the check result, e.g. "error", "warning", "info". This can help LLM to prioritize the improvement plan.
        details: Structured data for the check result, can include any additional information that can help 
        LLM to understand the failure and generate improvement plan, e.g. {"collision_count": 5, "first_collision_at": "link3"}.
        suggestion: Optional suggestion for improvement. Can serve as hints to both human and LLM for next steps to fix the issue. 
        For example, "Change the path to avoid collisions, or check the collision map for details."
    
    Examples:
        # Simple usage (backwards compatible)
        >>> result = CheckResult(is_valid=False, message="Number of collisions: 5")
        
        # Enhanced (atomic)
        >>> result = CheckResult(
        ...     is_valid=False,
        ...     message="Number of collisions: 5",
        ...     category="collision",
        ...     level=CheckResultLevel.ERROR,
        ...     details={"collision_count": 5, "first_collision_at": "link3"},
        ...     suggestion="Change the path to avoid collisions, or check the collision map for details."
        ... )
        
    """
    
    is_valid: bool
    message: str = ""
    category: Optional[str] = None
    level: Literal["error", "warning", "info"] = CheckResultLevel.INFO  
    details: Optional[Dict[str, Any]] = None
    suggestion: Optional[str] = None
    
    def toPlainText(self) -> str:
        # Convert the structured result to a plain text message for human readability, except details which can be too verbose for plain text.
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
        # Convert the check result to a structured message for LLM understanding and programmatic parsing.
        return asdict(self)