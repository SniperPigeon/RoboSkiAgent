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

    def get_object_position(self, name: str) -> dict:
        return self.runtime.get_object_position(name)

    def compute_pick_pose(self, name: str, grasp_profile: str | None = None) -> dict:
        return self.runtime.compute_pick_pose(name, grasp_profile)


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
        from SkiLib.base import SkillResult
        from SkiLib.tool_schema import build_pydantic_schema, resolve_tool_kwargs

        tools: list[StructuredTool] = []

        def _make_tool(primitive, parameters: dict):
            def _invoke(**kwargs) -> dict:
                resolved_kwargs, err = resolve_tool_kwargs(self.runtime, parameters, kwargs)
                if err is not None:
                    return err.to_llm_message()

                result = primitive.try_execute(**resolved_kwargs)
                return result.to_llm_message() if isinstance(result, SkillResult) else result

            return _invoke

        for class_name, primitive in self._primitives.items():
            parameters = getattr(primitive, "TOOL_PARAMETERS", None)
            if not parameters:
                logger.debug(
                    "PrimitiveRegistry: '%s' has no TOOL_PARAMETERS, skipping tool exposure.",
                    class_name,
                )
                continue

            tool_name = getattr(primitive, "TOOL_NAME", None) or class_name
            description = (
                getattr(primitive, "TOOL_DESCRIPTION", "")
                or primitive.__doc__
                or f"{tool_name} primitive"
            )
            args_schema = build_pydantic_schema(tool_name, parameters)

            tools.append(StructuredTool(
                name=tool_name,
                description=description.strip(),
                func=_make_tool(primitive, parameters),
                args_schema=args_schema,
            ))

        return tools
