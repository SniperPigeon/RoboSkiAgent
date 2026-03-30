import importlib
import pathlib

from robodk import robolink    # RoboDK API
from robodk import robomath    # Robot toolbox
from typing import Dict, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from SkiLib.base import BasePrimitive

# Forward and backwards compatible use of the RoboDK API:
# Remove these 2 lines to follow python programming guidelines
from robodk import *      # type: ignore # RoboDK API
from robolink import *  # type: ignore


# ============= Robot Context (Singleton) =============
class RobotContext:
    """
    Robot context singleton - manages RoboDK connection and primitive registry.
    
    Usage:
        # Initialize once at program start
        context = RobotContext()
        
        # Access primitives
        moveJ = context.primitives.get('MoveJ')
        
        # Or get registry directly
        registry = context.primitive_registry
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def instance(cls) -> Optional['RobotContext']:
        """Return the existing singleton if initialized, else None (no side effects)."""
        inst = cls._instance
        if inst is not None and getattr(inst, '_initialized', False):
            return inst
        return None

    def __init__(self):
        # Prevent re-initialization
        if self._initialized:
            return

        self.RDK = robolink.Robolink()
        robot = self.RDK.Item('', robolink.ITEM_TYPE_ROBOT)
        if robot is None or not robot.Valid():
            raise Exception("No robot found in the RoboDK station")
        self.robot = robot
        self.robot_name = robot.Name()

        # Safety flag: set True by Context Flush on failure; cleared on resume
        self.halt_flag: bool = False

        # Debug flag: when True, BaseSkill._should_skip_check() returns True so
        # try_execute() calls execute() directly without running check().
        # For simulation / unit-test environments only — never set True in production.
        self.debug_skip_check: bool = False

        # Auto-initialize primitive registry
        self.primitive_registry = PrimitiveRegistry(self.robot, self.RDK)

        # SkillRegistry eager init — must follow PrimitiveRegistry so primitives are ready
        from SkiLib.registry import SkillRegistry  # local import avoids circular dependency
        SkillRegistry().set_robot_context(self)

        self._initialized = True

    @property
    def primitives(self) -> Dict[str, 'BasePrimitive']:
        """Quick access to primitive instances"""
        return self.primitive_registry.get_all()

    def get_current_state(self):
        """
        Capture current robot state snapshot.
        Returns RobotState with None joints/pose if the robot is unreachable.
        Suitable for initialising GlobalState.robot_state.
        """
        from SkiLib.base import RobotState  # local import avoids circular dependency
        try:
            return RobotState(
                joints=list(self.robot.Joints()),
                pose=self.robot.Pose(),
                gripper_state="UNKNOWN",
            )
        except Exception:
            return RobotState(gripper_state="UNKNOWN")

    @property
    def is_simulation(self) -> bool:
        """True when RoboDK is running in simulation mode (not connected to real hardware)."""
        return self.RDK.RunMode() == robolink.RUNMODE_SIMULATE

    def get_gripper_state(self) -> dict:
        """
        Return current gripper state as a symbol-only dict (no coordinates).

        Simulation: infers grasped objects from RoboDK parent-child relationships.
        Real robot: reads digital IO signal from gripper sensor.

        Returns:
            {
                "active_tool": str,       # name of the currently active tool
                "grasped": list[str],     # names of objects currently held by the gripper
            }
        """
        tool = self.robot.getLink(robolink.ITEM_TYPE_TOOL)
        if not tool.Valid():
            return {"active_tool": None, "grasped": []}

        if self.is_simulation:
            grasped = [c.Name() for c in tool.Childs()
                       if c.Type() == robolink.ITEM_TYPE_OBJECT]
        else:
            # TODO: replace with actual IO pin from config when real-robot support is added
            raise NotImplementedError(
                "Real-robot gripper state requires IO configuration. "
                "Set self.config.gripper_sensor_pin and implement DI read."
            )

        return {"active_tool": tool.Name(), "grasped": grasped}


# ============= Primitive Registry (Singleton) =============
class PrimitiveRegistry:
    """
    Manages all primitive instances. Auto-discovers and instantiates primitives.
    
    Architecture:
        - Primitives are RoboDK-specific implementations
        - Skills depend only on BasePrimitive interface (decoupled from RoboDK)
        - Registry handles lifecycle and dependency injection
    
    Usage:
        # Usually created automatically by RobotContext
        registry = PrimitiveRegistry(robot, RDK)
        
        # Get specific primitive
        moveJ = registry.get('MoveJ')
        
        # Get all primitives (for skill initialization)
        all_primitives = registry.get_all()
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, robot: robolink.Item, RDK: robolink.Robolink):
        # Prevent re-initialization
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self.robot = robot
        self.RDK = RDK
        self._primitives: Dict[str, 'BasePrimitive'] = {}
        
        # Auto-register all primitives
        self._auto_register_primitives()
        self._initialized = True
    
    def _auto_register_primitives(self):
        """
        Auto-discover and instantiate all primitives from primitives.motion module.
        # FIXED: Now it scans the entire primitives/ folder for any primitive modules, and imports all classes that are subclass of BasePrimitive. 
        # This way we can add new primitives just by adding new files in primitives/ without modifying this registry code.
        
        """
        from SkiLib.primitives import motion
        from SkiLib.base import BasePrimitive
        import inspect
        
        
        primitives_dir = pathlib.Path(__file__).parent / "primitives"
        for py_file in sorted(primitives_dir.glob("*.py")):
            # Skip non-python files and private modules (starting with _), such as __init__.py or _utils.py
            if py_file.name.startswith("_"):
                continue
            # Get the relative path to convert to module name. __file__.parent.parent is the SkiLib/ folder, 
            # so relative path will be like "primitives/motion.py"
            relative = py_file.relative_to(pathlib.Path(__file__).parent.parent)
            module_name = str(relative.with_suffix("")).replace("/", ".").replace("\\", ".") # Deal with Windows path style
            # After conversion module_name will be like "Skilib.primitives.motion"
            
            try:
                module = importlib.import_module(module_name)
            except ImportError as e:
                print(f"[PrimitiveRegistry] WARNING: Failed to import {module_name}: {e}")
                continue
            
            # import module
            for name, cls in inspect.getmembers(module, inspect.isclass):
                # Get all primitives that are subclass of BasePrimitive, but not BasePrimitive itself
                if (issubclass(cls, BasePrimitive) and cls is not BasePrimitive and cls.__module__ == module_name): # Last is to avoid external imports
                    instance = cls(self.robot, self.RDK)
                    self._primitives[name] = instance
                    print(f"[PrimitiveRegistry] Registered: {name}")
                    


    def get(self, name: str) -> 'BasePrimitive':
        """Get primitive by name (e.g., 'MoveJ')"""
        if name not in self._primitives:
            raise KeyError(f"Primitive '{name}' not found. Available: {list(self._primitives.keys())}")
        return self._primitives[name]
    
    def get_all(self) -> Dict[str, 'BasePrimitive']:
        """Get all registered primitives as dict {name: instance}"""
        return self._primitives.copy()
    
    def register(self, name: str, primitive: 'BasePrimitive'):
        """Manually register a primitive (for custom/extension primitives)"""
        self._primitives[name] = primitive
        print(f"[PrimitiveRegistry] Manually registered: {name}")
    