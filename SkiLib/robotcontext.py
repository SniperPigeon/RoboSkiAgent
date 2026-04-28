import importlib
import inspect
import pathlib
from typing import Dict, Optional, TYPE_CHECKING

from SkiLib.genesis.runtime import GenesisRuntime
from SkiLib.log import get_logger

if TYPE_CHECKING:
    from SkiLib.base import BasePrimitive

logger = get_logger(__name__)


class RobotContext:
    """
    Genesis robot context singleton.

    This class intentionally keeps the old RobotContext name so Agent and
    SkillRegistry call sites do not need to change while the runtime switches
    from RoboDK to Genesis.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def instance(cls) -> Optional["RobotContext"]:
        inst = cls._instance
        if inst is not None and getattr(inst, "_initialized", False):
            return inst
        return None

    def __init__(self):
        if self._initialized:
            return

        self.runtime = GenesisRuntime()
        self.robot = self.runtime.robot
        self.scene = self.runtime.scene
        self.robot_name = self.runtime.robot_name

        self.halt_flag: bool = False
        self.debug_skip_check: bool = False

        self.primitive_registry = PrimitiveRegistry(self.runtime)

        from SkiLib.registry import SkillRegistry  # local import avoids circular dependency

        SkillRegistry().set_robot_context(self)

        from SkiLib.sensors import SensorRegistry  # local import avoids circular dependency

        self.sensor_registry = SensorRegistry()
        self.sensor_registry.initialize()

        self._initialized = True
        logger.info("RobotContext initialized with Genesis runtime.")

    @property
    def primitives(self) -> Dict[str, "BasePrimitive"]:
        return self.primitive_registry.get_all()

    @property
    def sensor_tools(self) -> list:
        return self.sensor_registry.get_tools()

    @property
    def is_simulation(self) -> bool:
        return self.runtime.is_simulation

    def get_current_state(self):
        return self.runtime.get_current_state()

    def get_gripper_state(self) -> dict:
        return self.runtime.get_gripper_state()

    def list_targets(self) -> list[str]:
        return self.runtime.list_targets()

    def list_objects(self) -> list[str]:
        return self.runtime.list_objects()

    def list_tools(self) -> list[str]:
        return self.runtime.list_tools()

    def check_item_exists(self, name: str) -> bool:
        return self.runtime.check_item_exists(name)

    def resolve_target(self, name: str):
        return self.runtime.resolve_target(name)

    def resolve_object(self, name: str):
        return self.runtime.resolve_object(name)

    def resolve_item(self, name: str):
        return self.runtime.resolve_item(name)


class PrimitiveRegistry:
    """Auto-discovers and instantiates Genesis primitive classes."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, runtime: GenesisRuntime):
        if self._initialized:
            return

        self.runtime = runtime
        self._primitives: Dict[str, "BasePrimitive"] = {}
        self._auto_register_primitives()
        self._initialized = True

    def _auto_register_primitives(self) -> None:
        from SkiLib.base import BasePrimitive

        primitives_dir = pathlib.Path(__file__).parent / "primitives"
        for py_file in sorted(primitives_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            relative = py_file.relative_to(pathlib.Path(__file__).parent.parent)
            module_name = str(relative.with_suffix("")).replace("/", ".").replace("\\", ".")

            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.error("PrimitiveRegistry: failed to import '%s': %s", module_name, e, exc_info=True)
                continue

            for name, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, BasePrimitive) and cls is not BasePrimitive and cls.__module__ == module_name:
                    try:
                        self._primitives[name] = cls(self.runtime)
                        logger.info("PrimitiveRegistry: registered '%s'.", name)
                    except Exception as e:
                        logger.error(
                            "PrimitiveRegistry: failed to instantiate '%s': %s",
                            name,
                            e,
                            exc_info=True,
                        )

    def get(self, name: str) -> "BasePrimitive":
        if name not in self._primitives:
            raise KeyError(f"Primitive '{name}' not found. Available: {list(self._primitives.keys())}")
        return self._primitives[name]

    def get_all(self) -> Dict[str, "BasePrimitive"]:
        return self._primitives.copy()

    def register(self, name: str, primitive: "BasePrimitive") -> None:
        self._primitives[name] = primitive
        logger.info("PrimitiveRegistry: manually registered '%s'.", name)

    def as_tools(self) -> list:
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field as PField

        from SkiLib.base import SkillResult, ExecutionPhase, ERROR_INVALID_PARAM

        tools: list[StructuredTool] = []

        def _resolve_target(name: str):
            try:
                return self.runtime.resolve_target(name), None
            except KeyError:
                err = SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_INVALID_PARAM,
                    message=f"Target '{name}' not found in the Genesis scene.",
                    suggestion="Use list_targets() to see valid target names.",
                )
                return None, err

        def _resolve_object(name: str):
            try:
                return self.runtime.resolve_object(name), None
            except KeyError:
                err = SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_INVALID_PARAM,
                    message=f"Object '{name}' not found in the Genesis scene.",
                    suggestion="Use list_objects() to see valid object names.",
                )
                return None, err

        if "MoveJ" in self._primitives:
            prim_movej = self._primitives["MoveJ"]

            class MoveJSchema(BaseModel):
                target: str = PField(description="Genesis target name to move to with joint motion.")

            def _movej(target: str) -> dict:
                item, err = _resolve_target(target)
                if err:
                    return err.to_llm_message()
                return prim_movej.try_execute(target=item).to_llm_message()

            tools.append(StructuredTool(
                name="MoveJ",
                description="Move the robot to a named Genesis target using joint motion.",
                func=_movej,
                args_schema=MoveJSchema,
            ))

        if "MoveL" in self._primitives:
            prim_movel = self._primitives["MoveL"]

            class MoveLSchema(BaseModel):
                target: str = PField(description="Genesis target name to move to with Cartesian linear motion.")

            def _movel(target: str) -> dict:
                item, err = _resolve_target(target)
                if err:
                    return err.to_llm_message()
                return prim_movel.try_execute(target=item).to_llm_message()

            tools.append(StructuredTool(
                name="MoveL",
                description="Move the TCP to a named Genesis target using linear Cartesian motion.",
                func=_movel,
                args_schema=MoveLSchema,
            ))

        if "Grasp" in self._primitives:
            prim_grasp = self._primitives["Grasp"]

            class GraspSchema(BaseModel):
                expected_item: str = PField(description="Genesis object name of the workpiece to grasp.")

            def _grasp(expected_item: str) -> dict:
                item, err = _resolve_object(expected_item)
                if err:
                    return err.to_llm_message()
                return prim_grasp.try_execute(expected_item=item).to_llm_message()

            tools.append(StructuredTool(
                name="Grasp",
                description="Close the gripper around a named Genesis workpiece.",
                func=_grasp,
                args_schema=GraspSchema,
            ))

        if "Release" in self._primitives:
            prim_release = self._primitives["Release"]

            class ReleaseSchema(BaseModel):
                expected_item: str = PField(description="Genesis object name of the workpiece to release.")

            def _release(expected_item: str) -> dict:
                item, err = _resolve_object(expected_item)
                if err:
                    return err.to_llm_message()
                return prim_release.try_execute(expected_item=item).to_llm_message()

            tools.append(StructuredTool(
                name="Release",
                description="Open the gripper and release the currently held Genesis workpiece.",
                func=_release,
                args_schema=ReleaseSchema,
            ))

        return tools
