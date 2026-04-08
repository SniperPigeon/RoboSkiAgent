"""
SkillMdLoader — parse SkiLib/skills/*.md files into SkillSpec objects.

Each .md file uses YAML frontmatter (between '---' delimiters) for structured
metadata (name, description, parameters schema) and a Markdown body for the
LLM execution guide.  This module converts the frontmatter parameters dict into
a Pydantic BaseModel subclass so Planner can use it directly as a StructuredTool
args_schema without any manual Pydantic code per skill.

Usage:
    loader = SkillMdLoader.instance()
    spec   = loader.get("PickAndPlace")
    # spec.args_schema  → Pydantic model (for Planner tool generation)
    # spec.body         → Markdown text  (for Executor sub-agent prompt)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, get_args

import yaml
from pydantic import BaseModel, Field, create_model

from SkiLib.log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

_PRIMITIVE_TYPES: dict[str, type] = {
    "str":   str,
    "int":   int,
    "float": float,
    "bool":  bool,
}


def _build_annotation(param_def: dict[str, Any]) -> Any:
    """
    Convert a single parameter definition dict to a Python type annotation.

    Supports:
    - Plain types:  type: str | int | float | bool
    - Enum:         type: str + enum: ["A", "B"]  →  Literal["A", "B"]
    - Optional:     required: false  →  Optional[T]
    """
    raw_type = param_def.get("type", "str")
    base_type = _PRIMITIVE_TYPES.get(raw_type, str)

    enum_values = param_def.get("enum")
    if enum_values:
        from typing import Literal
        # Build Literal[...] from enum list; values must match base_type
        annotation = Literal[tuple(enum_values)]  # type: ignore[valid-type]
    else:
        annotation = base_type

    required = param_def.get("required", True)
    if not required:
        annotation = Optional[annotation]  # type: ignore[assignment]

    return annotation


# ---------------------------------------------------------------------------
# SkillSpec
# ---------------------------------------------------------------------------

@dataclass
class SkillSpec:
    """Parsed representation of a single skill .md file."""

    name: str
    description: str
    category: str
    version: str
    required_primitives: list[str]
    args_schema: type[BaseModel]       # dynamically generated Pydantic model
    body: str                          # Markdown body — injected into Executor sub-agent prompt
    _raw_parameters: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# SkillMdLoader
# ---------------------------------------------------------------------------

class SkillMdLoader:
    """
    Singleton that scans SkiLib/skills/*.md and builds SkillSpec objects.

    Ignores *.py files in the same directory (legacy Python skills kept for
    backward compatibility).

    Thread-safety: not required — skills are loaded once at startup.
    """

    _instance: Optional["SkillMdLoader"] = None

    # Default skills directory: <this_file>/../skills/
    _DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

    def __init__(self, skills_dir: Optional[Path] = None):
        self._skills_dir = skills_dir or self._DEFAULT_SKILLS_DIR
        self._specs: dict[str, SkillSpec] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls, skills_dir: Optional[Path] = None) -> "SkillMdLoader":
        """Return the singleton, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls(skills_dir)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton (useful for tests that need a fresh loader)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all(self) -> dict[str, SkillSpec]:
        """Return all loaded skill specs keyed by skill name."""
        return dict(self._specs)

    def get(self, name: str) -> SkillSpec:
        """Return the SkillSpec for *name*. Raises KeyError if not found."""
        return self._specs[name]

    def has(self, name: str) -> bool:
        """Return True if *name* is a registered skill."""
        return name in self._specs

    def list_skills(self) -> list[str]:
        """Return sorted list of registered skill names."""
        return sorted(self._specs.keys())

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        """Scan skills_dir for *.md files and parse each one."""
        if not self._skills_dir.exists():
            logger.warning("SkillMdLoader: skills directory not found: %s", self._skills_dir)
            return

        md_files = sorted(self._skills_dir.glob("*.md"))
        if not md_files:
            logger.warning("SkillMdLoader: no *.md files found in %s", self._skills_dir)
            return

        for path in md_files:
            try:
                spec = self._parse_md(path)
                self._specs[spec.name] = spec
                logger.debug("SkillMdLoader: loaded skill '%s' from %s", spec.name, path.name)
            except Exception as exc:
                logger.error("SkillMdLoader: failed to parse %s: %s", path.name, exc, exc_info=True)

        logger.info("SkillMdLoader: loaded %d skill(s): %s", len(self._specs), list(self._specs.keys()))

    def _parse_md(self, path: Path) -> SkillSpec:
        """
        Split a .md file into YAML frontmatter and Markdown body.

        Expected format:
            ---
            <yaml frontmatter>
            ---
            <markdown body>
        """
        raw = path.read_text(encoding="utf-8")

        # Split on the '---' frontmatter delimiters.
        # Pattern: optional leading whitespace, then --- on its own line.
        parts = re.split(r"^---\s*$", raw, maxsplit=2, flags=re.MULTILINE)
        if len(parts) < 3:
            raise ValueError(
                f"File {path.name} is missing YAML frontmatter delimiters (---). "
                "Expected format: ---\\n<yaml>\\n---\\n<body>"
            )

        _pre, frontmatter_text, body = parts[0], parts[1], parts[2]

        meta: dict = yaml.safe_load(frontmatter_text) or {}

        name        = meta.get("name") or path.stem
        description = meta.get("description", "")
        category    = meta.get("category", "general")
        version     = str(meta.get("version", "1.0"))
        req_prims   = meta.get("required_primitives") or []
        parameters  = meta.get("parameters") or {}

        args_schema = self._build_pydantic_schema(name, parameters)

        return SkillSpec(
            name=name,
            description=description,
            category=category,
            version=version,
            required_primitives=list(req_prims),
            args_schema=args_schema,
            body=body.strip(),
            _raw_parameters=parameters,
        )

    def _build_pydantic_schema(
        self,
        skill_name: str,
        parameters: dict[str, Any],
    ) -> type[BaseModel]:
        """
        Dynamically build a Pydantic BaseModel from a parameters dict.

        Each entry in *parameters* has the shape:
            field_name:
                type: str | int | float | bool
                required: true | false   (default: true)
                default: <value>         (only used when required: false)
                description: "..."
                enum: [...]              (optional; produces Literal type)

        Returns a new BaseModel subclass named "<SkillName>Params".
        """
        if not parameters:
            # No parameters: return an empty model
            return create_model(f"{skill_name}Params")  # type: ignore[return-value]

        field_definitions: dict[str, Any] = {}

        for field_name, param_def in parameters.items():
            annotation  = _build_annotation(param_def)
            description = param_def.get("description", "")
            required    = param_def.get("required", True)
            default_val = param_def.get("default", ...)  # Pydantic uses ... for required

            if not required and default_val is ...:
                default_val = None  # Optional fields default to None if no default given

            field_info = Field(default=default_val, description=description)
            field_definitions[field_name] = (annotation, field_info)

        model = create_model(f"{skill_name}Params", **field_definitions)  # type: ignore[call-overload]
        return model  # type: ignore[return-value]
