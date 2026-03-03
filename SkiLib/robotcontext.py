from robodk import robolink    # RoboDK API
from robodk import robomath    # Robot toolbox
from typing import Dict, Type, TYPE_CHECKING

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
    
    def __init__(self):
        # Prevent re-initialization
        if self._initialized:
            return
            
        self.RDK = robolink.Robolink()
        robot = self.RDK.Item('', ITEM_TYPE_ROBOT)
        if robot is None or not robot.Valid():
            raise Exception("No robot found in the RoboDK station")
        self.robot = robot
        self.robot_name = robot.Name()
        
        # Auto-initialize primitive registry
        self.primitive_registry = PrimitiveRegistry(self.robot, self.RDK)
        self._initialized = True
    
    @property
    def primitives(self) -> Dict[str, 'BasePrimitive']:
        """Quick access to primitive instances"""
        return self.primitive_registry.get_all()
    
    # TODO Provide all variables in RoboDK Tree for LLM to access. But not sure where to put those.


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
        # TODO currently every primitives need to be imported here manually. (Hardcoded), in future we can use importlib to \
            perform a dynamic scan of all modules in the primitives/ folder and register any class that inherits from BasePrimitive. \
                This way we can add new primitives without modifying this registry code, \
                    just by placing them in the primitives/ folder.
        
        Future: Can use importlib to scan all modules in primitives/ folder.
        """
        from SkiLib.primitives import motion
        from SkiLib.base import BasePrimitive
        import inspect
        
        # Scan motion module for BasePrimitive subclasses
        # TODO Hardcoded!!!! if more primitive modules are added, 
        # we need to add more scan code here. 
        for name, obj in inspect.getmembers(motion, inspect.isclass):
            if issubclass(obj, BasePrimitive) and obj is not BasePrimitive:
                instance = obj(self.robot, self.RDK)
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
    