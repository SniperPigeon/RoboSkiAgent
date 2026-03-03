# Development Notes - Skill Registry System

**Date**: Feb 27, 2026  
**Status**: Design Complete, Implementation Pending

---

## Motivation

Current manual skill management (`Skills = {"moveJ": motion.moveJ(...)}`) has scalability issues:
- Manual registration for each new skill
- No standardized metadata for LLM integration
- Difficult to generate tool schemas for LLM agent
- No automatic discovery mechanism
- Lack of structured error reporting for self-correction

Need an automatic registration system that:
1. Auto-discovers skills via decorators
2. Generates LLM-compatible tool schemas
3. Provides singleton pattern for robot context
4. Enables eager loading with structured metadata

---

## Implementation Approach

### Architecture Overview

```
Module Import (Phase 0)
    ↓ @skill decorator executes
SkillRegistry stores class + metadata
    ↓
main.py: set_robot_context()
    ↓ Eager initialization
All skill instances created
    ↓ LLM schema generation
Cached tool definitions (OpenAI/Anthropic format)
    ↓
Runtime: registry.get_skill() or registry['name']
```

### Key Components

**1. Decorator-Based Registration**
```python
@skill(name="moveJ", description="...", category="primitive", parameters=[...])
class moveJ(BaseSkill):
    pass
```
- Executes at import time
- Stores skill class (not instance) + metadata
- Zero runtime overhead

**2. Singleton Patterns**
- **SkillRegistry**: Global singleton manages all skills
- **RobotContext**: Singleton connection to RoboDK
- **Skill Instances**: One instance per skill type (stateless design)

**3. Eager Initialization**
- All skills instantiated when `set_robot_context()` called
- Fails fast if any skill has initialization errors
- Acceptable since skill count is small (<20)

**4. Metadata System**
```python
SkillMetadata:
  - name, description, category (primitive/skill/task)
  - parameters: List[SkillParameter] (with JSON schema)
  - preconditions, effects, examples
  - to_llm_tool_schema() → LLM function calling format
```

**5. Auto-Discovery**
- `SkiLib/__init__.py` uses `pkgutil` to import all modules
- Importing triggers `@skill` decorators
- No manual imports needed

**6. Enhanced CheckResult**
```python
CheckResult:
  - is_valid, message
  - issues: List[CheckIssue] (severity, check_name, suggestion)
  - get_llm_feedback() → formatted for LLM self-correction
```

### Lifecycle

```
1. Import SkiLib → Auto-discover skills (decorators run)
2. RobotContext() → Connect to RoboDK (singleton)
3. registry.set_robot_context() → Eager load all skills
4. registry.list_skills() → ['moveJ', 'moveL', ...]
5. registry.get_llm_tool_schemas() → LLM tools (cached)
6. registry['moveJ'].execute() → Use skill
```

---

## Benefits

### For Development
- **No manual registration**: Just add `@skill` decorator
- **Type safety**: SkillMetadata provides structure
- **Fail fast**: Eager loading catches errors at startup
- **Introspection**: `registry.list_skills()`, `get_metadata()`

### For LLM Integration
- **Auto tool generation**: `get_llm_tool_schemas()` for OpenAI/Anthropic
- **Structured errors**: `CheckResult.get_llm_feedback()` enables self-correction
- **Rich metadata**: Preconditions, effects, examples in schema
- **Category filtering**: Only expose primitives vs. all skills

### For Extensibility  
- **Plugin-like**: Add new skill files, auto-discovered
- **Versioning ready**: Metadata includes version field
- **Multiple formats**: Easy to add new LLM providers
- **Context provision**: RobotContext can list all items/frames for LLM

### For Safety
- **Singleton robot**: Prevents multiple connections
- **Validation before exec**: `check()` → structured feedback
- **Collision state safety**: Context manager pattern (TODO)

---

## Technical Decisions

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Loading** | Eager | Small skill count, fail fast preferred |
| **Robot Context** | Singleton, external | Decouple from registry, testable |
| **Skill Instances** | Singleton | Skills are stateless |
| **LLM Schemas** | Generated at startup, cached | Doesn't change at runtime |
| **Discovery** | Auto-import via pkgutil | Zero-config for developers |
| **Error Handling** | CheckResult with issues list | Structured for LLM parsing |

---

## Implementation Status

- [x] Design complete
- [x] Detailed implementation plan written
- [ ] Create `registry.py`
- [ ] Create `decorators.py`
- [ ] Update `base.py` (CheckResult, SkillMetadata)
- [ ] Update `robotcontext.py` (singleton)
- [ ] Update `__init__.py` (auto-discovery)
- [ ] Add `@skill` to existing primitives
- [ ] Update `main.py`
- [ ] Write unit tests
- [ ] Verify LLM schema generation

---

## Next Steps

1. Implement `registry.py` and `decorators.py`
2. Refactor `CheckResult` with enhanced feedback
3. Add singleton pattern to `RobotContext`
4. Update `moveJ` primitive with decorator
5. Test auto-discovery mechanism
6. Generate and validate LLM tool schemas
7. Document usage examples for future skills

Goal: Infrastructure ready before implementing more primitives (moveL, grasp, release). Once registry works, adding new skills becomes trivial.

---

## Notes

- **Collision state management**: Still needs context manager fix (TODO from previous review)
- **IK closest solution**: Separate enhancement, independent of registry
- **Human-in-the-loop**: Will build on top of this registry system
- Consider adding `registry.refresh()` for development hot-reload
