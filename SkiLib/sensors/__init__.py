"""
Execution-time perception sensors for the LLM Executor's ReAct loop.

Design intent
-------------
Sensors are **read-only observation tools** — the Executor calls them *after*
an action primitive to verify the physical outcome before deciding next steps.

Distinction from metatools
--------------------------
metatools/   → planning-time, symbolic queries  (Supervisor uses these)
sensors/     → execution-time, physical queries (Executor uses these)

Auto-discovery
--------------
SensorRegistry scans sensors/*.py at construction time and aggregates every
module-level get_tools() list.  Adding a new sensor module requires no
registration boilerplate — just implement get_tools() and drop the file in.

Usage
-----
    # Accessed via RobotContext (initialised automatically on first RobotContext())
    from SkiLib.robotcontext import RobotContext
    tools = RobotContext.instance().sensor_registry.get_tools()

    # Or directly (after RobotContext is already initialised)
    from SkiLib.sensors import SensorRegistry
    tools = SensorRegistry.instance().get_tools()
"""

import importlib
import pathlib
from typing import List, Optional

from SkiLib.log import get_logger

logger = get_logger(__name__)


class SensorRegistry:
    """
    Singleton registry that auto-discovers all sensor modules in sensors/.

    Each sensor module must expose a get_tools() -> list function that returns
    LangChain tool objects.  This registry aggregates them into a single flat
    list for llm.bind_tools().

    Unlike SkillRegistry, no robot context injection is needed at construction:
    sensor functions resolve RobotContext lazily at call time.
    """

    _instance: Optional['SensorRegistry'] = None

    def __new__(cls) -> 'SensorRegistry':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools: List = []
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def instance(cls) -> Optional['SensorRegistry']:
        """Return the singleton if it exists, else None (no side effects)."""
        return cls._instance if (cls._instance and cls._instance._initialized) else None

    def initialize(self) -> None:
        """
        Scan sensors/ and collect all tools.  Called once by RobotContext.__init__().
        Calling again clears and re-discovers (useful for hot reload).
        """
        self._tools.clear()
        self._auto_discover()
        self._initialized = True

    def _auto_discover(self) -> None:
        """
        Glob sensors/*.py, skip _*.py, import each module, call get_tools().

        Mirrors PrimitiveRegistry._auto_register_primitives() scan strategy so
        new sensor modules need no manual registration.
        """
        sensors_dir = pathlib.Path(__file__).parent

        for py_file in sorted(sensors_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # skip __init__.py, _utils.py, etc.

            module_name = f"SkiLib.sensors.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.error(
                    "SensorRegistry: failed to import '%s': %s",
                    module_name, e, exc_info=True,
                )
                continue

            if not hasattr(module, "get_tools"):
                logger.debug(
                    "SensorRegistry: '%s' has no get_tools(), skipping.", py_file.name
                )
                continue

            tools = module.get_tools()
            self._tools.extend(tools)
            logger.info(
                "SensorRegistry: loaded %d tool(s) from '%s'.",
                len(tools), py_file.name,
            )

    def get_tools(self) -> List:
        """Return all discovered sensor tools, ready for llm.bind_tools()."""
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        names = [t.name for t in self._tools]
        return f"SensorRegistry(tools={names})"
