import importlib
import inspect
import pathlib
from typing import Dict, List, Optional, TYPE_CHECKING

from SkiLib.base import BaseSkill, MissingPrimitiveError
from SkiLib.log import get_logger

if TYPE_CHECKING:
    from SkiLib.robotcontext import RobotContext

logger = get_logger(__name__)


class SkillRegistry:
    """
    Singleton registry for high-level Skills.

    Mirrors PrimitiveRegistry: auto-discovers all BaseSkill subclasses in the
    skills/ directory, instantiates them with the current primitives dict, and
    exposes them as LangChain StructuredTool objects for Executor llm.bind_tools().

    Lifecycle:
        1. SkillRegistry() is created (empty, no robot context yet).
        2. RobotContext.__init__() calls SkillRegistry().set_robot_context(ctx)
           after PrimitiveRegistry is ready.
        3. set_robot_context() triggers _auto_register_skills(), which scans
           skills/*.py, finds BaseSkill subclasses, and instantiates them.

    Usage:
        sr = SkillRegistry.instance()
        skill = sr.get_skill('PickAndPlace')
        tools = sr.get_tools()                       # for llm.bind_tools()
        names = sr.list_skills(category='manipulation')
    """

    _instance: Optional['SkillRegistry'] = None

    def __new__(cls) -> 'SkillRegistry':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._skills: Dict[str, BaseSkill] = {}
            cls._instance._context = None
        return cls._instance

    @classmethod
    def instance(cls) -> Optional['SkillRegistry']:
        """Return the singleton if it exists, else None (no side effects)."""
        return cls._instance

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def set_robot_context(self, context: 'RobotContext') -> None:
        """
        Bind the robot context and eagerly instantiate all discovered Skills.
        Called once by RobotContext.__init__() after PrimitiveRegistry is ready.
        Calling again clears and re-registers all skills (useful for hot reload).
        """
        self._context = context
        self._skills.clear()
        self._auto_register_skills()

    def _auto_register_skills(self) -> None:
        """
        Auto-discover and instantiate all BaseSkill subclasses in skills/.

        Mirror of PrimitiveRegistry._auto_register_primitives():
          - Glob *.py in skills/ (skip _*.py)
          - importlib.import_module each file
          - inspect.getmembers → filter BaseSkill subclasses
          - cls.__module__ == module_name guard prevents double-registration
            when a class is re-imported from another module
          - cls(primitives_dict) to instantiate
        """
        from SkiLib.base import BasePrimitive  # local import avoids circularity

        primitives = self._context.primitive_registry.get_all()
        skills_dir = pathlib.Path(__file__).parent / "skills"

        if not skills_dir.exists():
            logger.warning("SkillRegistry: skills/ directory not found at %s", skills_dir)
            return

        for py_file in sorted(skills_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            # Build dotted module name (same strategy as PrimitiveRegistry)
            relative = py_file.relative_to(pathlib.Path(__file__).parent.parent)
            module_name = str(relative.with_suffix("")).replace("/", ".").replace("\\", ".")

            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.error(
                    "SkillRegistry: failed to import '%s': %s",
                    module_name, e, exc_info=True,
                )
                continue

            for name, cls in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(cls, BaseSkill)
                    and cls is not BaseSkill
                    and cls.__module__ == module_name  # guard: no external-import pollution
                ):
                    try:
                        instance = cls(primitives)
                        self._skills[name] = instance
                        logger.info("SkillRegistry: registered '%s'", name)
                    except MissingPrimitiveError as e:
                        logger.warning(
                            "SkillRegistry: skipping '%s' — missing primitives: %s",
                            name, e,
                        )
                    except Exception as e:
                        logger.error(
                            "SkillRegistry: failed to instantiate '%s': %s",
                            name, e, exc_info=True,
                        )

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def get_skill(self, name: str) -> BaseSkill:
        """Return the instantiated Skill by class name. Raises KeyError if not found."""
        if name not in self._skills:
            raise KeyError(
                f"Skill '{name}' not found. "
                f"Registered: {list(self._skills.keys())}"
            )
        return self._skills[name]

    def list_skills(self, category: Optional[str] = None) -> List[str]:
        """
        Return registered skill names.
        Pass category= to filter by SKILL_CATEGORY class variable.
        """
        if category is None:
            return list(self._skills.keys())
        return [
            name for name, skill in self._skills.items()
            if skill.SKILL_CATEGORY == category
        ]

    def get_tools(self) -> list:
        """
        Return a flat list of LangChain StructuredTool objects covering all
        registered skills' check / execute / try_execute methods.
        Suitable for llm.bind_tools().
        """
        tools = []
        for skill in self._skills.values():
            tools.extend(skill.as_tools())
        return tools

    def get_llm_tool_schemas(self, format: str = "anthropic") -> List[dict]:
        """
        Serialize tool schemas to a list of dicts for prompt injection.
        Currently delegates to LangChain's built-in schema generation.

        Args:
            format: Schema format — only "anthropic" supported for now.

        Returns:
            List of tool schema dicts, one per (skill, method) combination.
        """
        if format != "anthropic":
            raise ValueError(f"Unsupported schema format: '{format}'. Only 'anthropic' is supported.")
        tools = self.get_tools()
        # LangChain StructuredTool exposes args_schema (a Pydantic model).
        # Convert to the Anthropic tool spec format.
        schemas = []
        for tool in tools:
            schema = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.args_schema.schema() if tool.args_schema else {"type": "object", "properties": {}},
            }
            schemas.append(schema)
        return schemas

    # ------------------------------------------------------------------
    # Dict-like access
    # ------------------------------------------------------------------

    def __getitem__(self, name: str) -> BaseSkill:
        return self.get_skill(name)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={list(self._skills.keys())})"
