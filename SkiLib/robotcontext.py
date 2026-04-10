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

        # SensorRegistry: scan sensors/ and aggregate execution-time perception tools.
        # No context injection needed — sensor functions resolve RobotContext lazily.
        from SkiLib.sensors import SensorRegistry  # local import avoids circular dependency
        self.sensor_registry = SensorRegistry()
        self.sensor_registry.initialize()

        self._initialized = True

    @property
    def primitives(self) -> Dict[str, 'BasePrimitive']:
        """Quick access to primitive instances."""
        return self.primitive_registry.get_all()

    @property
    def sensor_tools(self) -> list:
        """Flat list of all execution-time sensor tools for llm.bind_tools()."""
        return self.sensor_registry.get_tools()

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

    # ------------------------------------------------------------------
    # V2: LLM-facing tool wrappers
    # ------------------------------------------------------------------

    def as_tools(self) -> list:
        """
        Return a LangChain StructuredTool list for all registered primitives.

        All tool parameters are symbolic strings (RoboDK item names) — no
        robolink.Item or robomath.Mat types are exposed to the LLM.
        Symbol resolution (str → RDK.Item) happens inside each wrapper so that
        invalid names produce ERROR_INVALID_PARAM rather than a Python exception.

        Currently wraps: MoveJ, MoveL, Grasp, Release.
        Other primitives are skipped (no LLM-facing signature defined for them).
        """
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field as PField

        from SkiLib.base import SkillResult, ExecutionPhase, ERROR_INVALID_PARAM

        tools: list[StructuredTool] = []
        RDK  = self.RDK

        # ---- helper: resolve symbol name → Item, return error SkillResult on failure ----
        def _resolve(name: str) -> tuple:
            item = RDK.Item(name)
            if not item.Valid():
                err = SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_INVALID_PARAM,
                    message=f"Symbol '{name}' not found in RoboDK station.",
                    suggestion="Use list_targets() or list_objects() to see valid names.",
                )
                return None, err
            return item, None

        # ---- MoveJ ----
        if "MoveJ" in self._primitives:
            prim_movej = self._primitives["MoveJ"]

            class MoveJSchema(BaseModel):
                target: str = PField(description="RoboDK target name to move to (joint motion).")

            def _movej(target: str) -> dict:
                item, err = _resolve(target)
                if err:
                    return err.to_llm_message()
                return prim_movej.try_execute(target=item).to_llm_message()

            tools.append(StructuredTool(
                name="MoveJ",
                description=(
                    "Move the robot to a named target using joint motion. "
                    "Safe for large moves between distant configurations. "
                    "target must be a valid RoboDK target name (use list_targets() to check)."
                ),
                func=_movej,
                args_schema=MoveJSchema,
            ))

        # ---- MoveL ----
        if "MoveL" in self._primitives:
            prim_movel = self._primitives["MoveL"]

            class MoveLSchema(BaseModel):
                target: str = PField(description="RoboDK target name to move to (linear Cartesian motion).")

            def _movel(target: str) -> dict:
                item, err = _resolve(target)
                if err:
                    return err.to_llm_message()
                return prim_movel.try_execute(target=item).to_llm_message()

            tools.append(StructuredTool(
                name="MoveL",
                description=(
                    "Move the robot to a named target using linear Cartesian motion. "
                    "Required for approach and retract near workpieces — do NOT substitute with MoveJ. "
                    "target must be a valid RoboDK target name (use list_targets() to check)."
                ),
                func=_movel,
                args_schema=MoveLSchema,
            ))

        # ---- Grasp ----
        if "Grasp" in self._primitives:
            prim_grasp = self._primitives["Grasp"]

            class GraspSchema(BaseModel):
                expected_item: str = PField(description="RoboDK object name of the workpiece to grasp.")

            def _grasp(expected_item: str) -> dict:
                item, err = _resolve(expected_item)
                if err:
                    return err.to_llm_message()
                return prim_grasp.try_execute(expected_item=item).to_llm_message()

            tools.append(StructuredTool(
                name="Grasp",
                description=(
                    "Close the gripper and attach the nearest object to the tool tip. "
                    "The robot must already be positioned at the pick target via MoveL before calling this. "
                    "expected_item must be the RoboDK object name of the intended workpiece."
                ),
                func=_grasp,
                args_schema=GraspSchema,
            ))

        # ---- Release ----
        if "Release" in self._primitives:
            prim_release = self._primitives["Release"]

            class ReleaseSchema(BaseModel):
                expected_item: str = PField(description="RoboDK object name of the workpiece to release.")

            def _release(expected_item: str) -> dict:
                item, err = _resolve(expected_item)
                if err:
                    return err.to_llm_message()
                return prim_release.try_execute(expected_item=item).to_llm_message()

            tools.append(StructuredTool(
                name="Release",
                description=(
                    "Open the gripper and release all objects attached to the tool. "
                    "The robot must already be positioned at the place target via MoveL before calling this. "
                    "expected_item must be the RoboDK object name of the intended workpiece."
                ),
                func=_release,
                args_schema=ReleaseSchema,
            ))

        return tools
