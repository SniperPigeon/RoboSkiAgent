# 改造方案：去掉 BaseSkill 层，LLM 直接调用 Primitives

## Context

当前架构中，`BaseSkill`（如 `PickAndPlace`）用 Python 硬编码固定步骤序列，LLM 只负责填入高层参数，真正的"怎么做"和"出错了怎么恢复"由代码决定。这导致：
1. 完全不测试 LLM 的指令遵循能力
2. 无法让 LLM 自主组合出新技能
3. 恢复逻辑硬编码，无法从 skill 描述泛化

**目标**：
- 去掉 `BaseSkill` Python 层，技能改为 `skills/*.md` 自然语言描述文件
- Planner 仍生成 skill 级别任务，但 schema 来自 skill.md 而非 Python 类
- Executor 启动有界 LLM sub-agent，sub-agent 读取 skill.md 后直接调用 Primitives，并依据 skill.md 的 recovery hints 处理错误重试
- **原有所有代码保持不变，平行新建一套 nodes + graph**

---

## 最终架构

```
Planner LLM
  ← 工具: add_<SkillName>_task（schema 来自 skill.md frontmatter）
  → todo_list: [{skill: "PickAndPlace", params: {pick_target, place_target, ...}}]

Dispatcher（复用原有）→ Executor V2（有界 sub-agent，skill 粒度）
  ← skill.md body（执行序列指导 + recovery hints）
  ← Primitive 工具: MoveJ, MoveL, Grasp, Release
  ← escalate_to_hitl
  → LLM 序列化 primitives，失败时按 recovery hints 决策：
      ✅ 可恢复（Grasp 失败）→ 回退到 pick_approach → 重试（无 HITL）
      ❌ 不可恢复（IK_FAILURE）→ escalate_to_hitl → 主图 HITL Handler
```

---

## skill.md 文件格式

路径：`SkiLib/skills/<SkillName>.md`

```markdown
---
name: PickAndPlace
description: "Pick an object from a source target and place it at a destination target."
category: manipulation
version: "1.0"

parameters:
  item:
    type: str
    required: true
    description: "RoboDK name of the workpiece."
  home_position:
    type: str
    required: true
    description: "Target name for safe start/end position."
  pick_approach:
    type: str
    required: true
    description: "Target name for approach/depart point above pick."
  pick_target:
    type: str
    required: true
    description: "Target name for precise grasp point."
  place_approach:
    type: str
    required: true
    description: "Target name for approach/depart point above place."
  place_target:
    type: str
    required: true
    description: "Target name for precise placement point."
  transit_motion:
    type: str
    required: false
    default: "MoveJ"
    enum: ["MoveJ", "MoveL"]
    description: "Motion type for pick→place transit."
  initial_motion:
    type: str
    required: false
    default: "MoveJ"
    enum: ["MoveJ", "MoveL"]
    description: "Motion type for initial home move."

required_primitives: [MoveJ, MoveL, Grasp, Release]
---

# PickAndPlace — Execution Guide

## Purpose
Move a workpiece from a pick location to a place location.
All parameters are RoboDK symbolic names — NEVER pass coordinates.

## Standard Execution Sequence
1. `{initial_motion}` → `home_position`
2. `MoveL` → `pick_approach`
3. `MoveL` → `pick_target`
4. `Grasp(expected_item=item)`
5. `MoveL` → `pick_approach`
6. `{transit_motion}` → `place_approach`
7. `MoveL` → `place_target`
8. `Release(expected_item=item)`
9. `MoveL` → `place_approach`
10. `MoveL` → `home_position`

## Recovery Hints
- **Grasp failure**: Re-execute MoveL to `pick_target` (step 3), then retry Grasp. Max 2 retries.
- **MoveL IK_FAILURE**: Call `escalate_to_hitl` immediately. Do NOT substitute MoveJ.
- **Release failure**: Retry once in place. If still fails, call `escalate_to_hitl`.
- **Any COLLISION error**: Call `escalate_to_hitl` immediately.

## Notes
- Call `get_gripper_state()` before Grasp/Release if unsure of current gripper status.
- After manual intervention (HITL resume), check gripper state before deciding where to restart.
```

---

## 新增文件列表（不修改任何原有文件）

```
Agent/
├── graph_v2.py                    # 新图（复用原有 nodes，替换 planner/executor）
├── nodes/
│   ├── planner_v2.py              # Planner：工具 schema 来自 skill.md
│   └── executor_v2.py             # Executor：sub-agent 直接调用 Primitives
└── prompts/
    └── skill_executor.txt         # Executor sub-agent 基础系统提示

SkiLib/
├── skill_loader.py                # SkillMdLoader + SkillSpec（解析 .md 文件）
└── skills/
    └── pick_and_place.md          # 第一个 md 技能文件
```

**原有代码完全不动**：`graph.py`、`nodes/planner.py`、`nodes/executor.py`、`SkiLib/registry.py`、`SkiLib/skills/pick_and_place.py` 等全部保留。

---

## 各新增文件设计

### 1. `SkiLib/skill_loader.py`

```python
@dataclass
class SkillSpec:
    name: str
    description: str
    category: str
    required_primitives: list[str]
    args_schema: type[BaseModel]   # 动态生成，供 Planner StructuredTool 使用
    body: str                     # Markdown body，注入 Executor sub-agent 系统提示

class SkillMdLoader:
    """Singleton. Scans SkiLib/skills/*.md, ignores *.py."""
    
    def get_all(self) -> dict[str, SkillSpec]: ...
    def get(self, name: str) -> SkillSpec: ...
    def has(self, name: str) -> bool: ...
    
    def _parse_md(self, path: Path) -> SkillSpec:
        """Split frontmatter/body via '---', parse YAML, build Pydantic schema."""
    
    def _build_pydantic_schema(self, skill_name: str, parameters: dict) -> type[BaseModel]:
        """
        pydantic.create_model() from parameters dict.
        - type: str/int/float/bool → native Python types
        - enum → Literal[...]
        - required=false + default → Optional with default
        Each field gets Field(description=...) from the md description.
        """
```

### 2. `SkiLib/skills/pick_and_place.md`

见上方 skill.md 格式，完整内容。

### 3. `Agent/nodes/planner_v2.py`

原 `planner.py` 的 drop-in 替换版，仅修改 `_make_planner_tools()`：

```python
# 原：从 SkillRegistry 获取 Python skill → as_tools() → args_schema
# 新：从 SkillMdLoader 获取 SkillSpec → spec.args_schema

def _make_planner_tools_v2() -> tuple[list[StructuredTool], list[dict]]:
    plan: list[dict] = []
    tools: list[StructuredTool] = []
    
    loader = SkillMdLoader.instance()
    for skill_name, spec in loader.get_all().items():
        def _create_task_adder(sname: str):
            def _add_task(**kwargs) -> str:
                task_id = f"t{len(plan) + 1}"
                plan.append({"task_id": task_id, "type": "auto", "skill": sname, "params": kwargs})
                return f"Task {task_id} ({sname}) added."
            return _add_task
        tools.append(StructuredTool(
            name=f"add_{skill_name}_task",
            description=spec.description,
            func=_create_task_adder(skill_name),
            args_schema=spec.args_schema,
        ))
    
    # add_manual_task 保持与原版一致
    ...
    return tools, plan

def planner_v2(state: GlobalState, *, llm) -> dict:
    # 与原 planner() 逻辑完全相同，仅调用 _make_planner_tools_v2()
    ...
```

supervisor 提示词中的技能发现部分也需适配（新建私有函数，不改 supervisor.py）：
- `planner_v2.py` 自己组装 skill 列表注入，或复用 SkillMdLoader 在 graph_v2 的 supervisor 节点中注入

### 4. `Agent/nodes/executor_v2.py`

```python
def executor_v2(state: GlobalState, *, llm) -> dict:
    task       = state["current_task"]
    skill_name = task.get("skill", "")
    params     = task.get("params", {})

    if state.get("halt_flag"):
        # 保持与原 executor 一致的 halt 检查逻辑
        ...

    loader = SkillMdLoader.instance()
    
    # V2 路径：skill.md 存在 → sub-agent 执行
    if loader.has(skill_name):
        spec = loader.get(skill_name)
        ctx = RobotContext.instance()
        primitive_tools = ctx.primitive_registry.as_tools()  # 见下方说明
        
        system_prompt = _build_executor_system_prompt(spec, params)
        sub_agent = create_agent(
            model=llm,
            tools=[escalate_tool, *primitive_tools, list_targets, get_gripper_state],
            system_prompt=system_prompt,
        )
        try:
            sub_agent.invoke({"messages": [HumanMessage(
                content=f"Execute {skill_name} with parameters: {params}"
            )]})
        except _EscalateHITLException as e:
            result = SkillResult(success=False, needs_hitl=True, ...)
            return {"last_result": result, "halt_flag": True, "halt_reason": "TASK_FAILURE", ...}
        
        result = SkillResult(success=True, ...)
        return {"last_result": result, "current_task": {}, ...}
    
    # Fallback：skill 不在 md 库中，日志警告
    return {"last_result": SkillResult(success=False, error_type="SKILL_NOT_FOUND", ...), ...}


def _build_executor_system_prompt(spec: SkillSpec, params: dict) -> str:
    return f"""You are a robot executor sub-agent for the {spec.name} skill.

## Skill Execution Guide
{spec.body}

## Concrete Parameters for This Execution
{params}

## Strict Rules
- Call primitives in the order described above.
- ALL primitive arguments must be symbolic names (strings) — NEVER pass coordinates or numbers.
- Follow Recovery Hints before calling escalate_to_hitl.
- After max retries exhausted, call escalate_to_hitl.
"""
```

**`PrimitiveRegistry.as_tools()` 的位置**：新增为 `robotcontext.py` 中 `PrimitiveRegistry` 的方法——这是 SkiLib 层，不影响 Agent 原有代码，且不破坏任何现有接口。仅新增方法，不修改已有方法。

### 5. `Agent/graph_v2.py`

```python
# 复用原有节点：supervisor, dispatcher, plan_review, manual_intervention_handler, hitl_handler
# 替换两个节点：planner → planner_v2，executor → executor_v2

from Agent.nodes.supervisor import supervisor
from Agent.nodes.planner_v2 import planner_v2 as planner
from Agent.nodes.plan_review import plan_review
from Agent.nodes.dispatcher import dispatcher
from Agent.nodes.executor_v2 import executor_v2 as executor
from Agent.nodes.manual_handler import manual_intervention_handler
from Agent.nodes.hitl_handler import hitl_handler

def build_graph_v2(llm=None) -> CompiledStateGraph:
    # 与原 build_graph() 结构完全相同，仅 planner/executor 换为 v2 版本
    ...
```

---

## 需要在 SkiLib/robotcontext.py 新增的一个方法

这是唯一对原有文件的修改，仅追加，不改动任何已有代码：

```python
# 在 PrimitiveRegistry 类末尾新增：
def as_tools(self) -> list:
    """
    Return LangChain StructuredTool list for all registered primitives.
    All tool parameters are str/int/float — no RoboDK types exposed.
    Symbol resolution (str → RDK.Item) happens inside each wrapper.
    """
```

具体实现需先确认各 Primitive 的 `try_execute` 实际签名（Step 0），然后为 `MoveJ` / `MoveL` / `Grasp` / `Release` 分别生成包装工具。

---

## 实现顺序

**Step 0（前置检查）**：
- 读 `SkiLib/primitives/motion.py` + `gripper.py`，记录每个 Primitive 的 `try_execute` 签名
- 确认哪些参数是 `robolink.Item`（需包装转换），哪些已是 `str`

**Step 1**：创建 `SkiLib/skill_loader.py`（`SkillSpec` + `SkillMdLoader`）

**Step 2**：创建 `SkiLib/skills/pick_and_place.md`，验证 SkillMdLoader 解析正确

**Step 3**：在 `SkiLib/robotcontext.py` 末尾新增 `PrimitiveRegistry.as_tools()` 方法

**Step 4**：创建 `Agent/nodes/planner_v2.py`

**Step 5**：创建 `Agent/prompts/skill_executor.txt` + `Agent/nodes/executor_v2.py`

**Step 6**：创建 `Agent/graph_v2.py`（复用原节点，替换 planner/executor）

**Step 7**：集成测试
- 运行 GUI（指定 `build_graph_v2`）
- 输入装配指令，验证 Planner 工具 schema 来自 skill.md
- 验证 Executor sub-agent 序列化 Primitives 并完成任务
- 验证 Grasp 失败场景：sub-agent 回退重试（不触发 HITL）
- 验证 IK 失败场景：escalate → HITL Handler 正确触发

---

## 关键风险

| 风险 | 缓解措施 |
|------|---------|
| LLM 传入坐标字符串而非符号名 | `as_tools()` 包装层 `RDK.Item(name).Valid()` 校验，失败返回 `ERROR_INVALID_PARAM`，不触发运动 |
| Sub-agent 忽略 recovery hints 直接 escalate | 系统提示强制要求先 follow Recovery Hints；测试用 Grasp 故意失败验证 |
| Sub-agent 无限重试 | Recovery hints 中 max_retries 约束；系统提示写明超出次数必须 escalate |
| HITL retry 后状态混乱 | Sub-agent 重新启动时调用 `get_gripper_state()` 判断当前状态 |
| Primitive 签名含非 str 类型 | Step 0 必须先确认，`as_tools()` 包装层处理转换 |

---

## 分步实现清单

每次对话从此列表选取 1 个未完成的 Step 推进。完成后在这里标记 `[x]`。

### Step 0 — 前置签名审查 `[x]`
- [x] 读 `SkiLib/primitives/motion.py`，记录 `MoveJ.try_execute` / `MoveL.try_execute` 完整签名
- [x] 读 `SkiLib/primitives/gripper.py`，记录 `Grasp.try_execute` / `Release.try_execute` 完整签名
- [x] 列出哪些参数是 `robolink.Item`（需包装），哪些已是 `str`
- [x] 将审查结果更新到本文件 "Primitive 签名记录" 小节

### Step 1 — `SkiLib/skill_loader.py` `[x]`
- [x] 实现 `SkillSpec` dataclass（name, description, category, required_primitives, args_schema, body）
- [x] 实现 `SkillMdLoader` 单例（`load_all` 扫描 `skills/*.md`，忽略 `.py`）
- [x] 实现 `_parse_md()`：以 `---` 分割 frontmatter/body，解析 YAML
- [x] 实现 `_build_pydantic_schema()`：`pydantic.create_model()`，支持 enum→Literal、Optional+default
- [x] 验证：解析 pick_and_place.md 正确，8 个字段，enum→Literal，required→PydanticUndefined

### Step 2 — `SkiLib/skills/pick_and_place.md` `[x]`
- [x] 按方案格式完整编写（frontmatter parameters + body 含 10 步序列 + Recovery Hints）
- [x] 验证：`SkillMdLoader.instance().get("PickAndPlace").args_schema.model_fields` 正确
- [x] 验证：`spec.body` 包含完整执行指导

### Step 3 — `PrimitiveRegistry.as_tools()` `[x]`
- [x] 在 `SkiLib/robotcontext.py` 的 `PrimitiveRegistry` 末尾新增 `as_tools()` 方法
- [x] 为 MoveJ / MoveL 生成包装工具（参数：`target: str`，内部 `RDK.Item(target)` 转换）
- [x] 为 Grasp / Release 生成包装工具（参数：`expected_item: str`）

### Step 4 — `Agent/nodes/planner_v2.py` `[x]`
- [x] 创建 `planner_v2.py`，工具 schema 来自 `SkillMdLoader`
- [x] `_make_planner_tools_v2()`：从 `SkillSpec.args_schema` 生成 add_<Skill>_task 工具
- [x] system_prompt 注入 Available Skills 摘要（来自 skill.md）
- [x] 无 `SkillRegistry` 依赖
- [x] 提供 `get_available_skills_from_md()` 辅助函数供 supervisor 使用

### Step 5 — `Agent/prompts/skill_executor.txt` + `Agent/nodes/executor_v2.py` `[x]`
- [x] 创建 `Agent/prompts/skill_executor.txt`（符号名规则、escalate 条件、recovery 优先级、HITL resume 提示）
- [x] 创建 `Agent/nodes/executor_v2.py`（`executor_v2` + `_build_executor_system_prompt` + `post_task_router_v2`）
- [x] 实现 sub-agent 启动逻辑（`create_agent` + `_EscalateHITLException` 捕获）
- [x] 实现成功/失败路径的 state 返回（`current_task: {}` 清空、`halt_flag` 设置）
- [x] 复用原 executor.py 的 `escalate_tool` / `_EscalateHITLException`（re-export）

### Step 6 — `Agent/graph_v2.py` `[x]`
- [x] 创建 `graph_v2.py`，与 `graph.py` 拓扑完全相同
- [x] 替换 planner → planner_v2，executor → executor_v2，post_task_router → post_task_router_v2
- [x] 保留其余所有节点（supervisor, dispatcher, plan_review, manual_handler, hitl_handler）不变
- [x] 导出 `build_graph_v2()` 和 `make_initial_state()` 函数
- [x] 导入验证通过（python -c 所有断言通过）

### Step 7 — 集成测试 `[ ]`
- [ ] 修改 GUI 或 `__main__.py` 支持选择 `build_graph_v2`
- [ ] 运行仿真：输入装配指令，检查 Planner 工具是否来自 skill.md
- [ ] 验证 Executor sub-agent 生成正确的 Primitive 调用序列
- [ ] 模拟 Grasp 失败：验证 sub-agent 回退 pick_approach → 重试（不进 HITL）
- [ ] 模拟 MoveL IK_FAILURE：验证 sub-agent escalate → HITL Handler 触发

---

## Primitive 签名记录（Step 0 填写）

| Primitive | 方法 | LLM 暴露参数 | 原始类型 | 包装方式 |
|-----------|------|------------|---------|---------|
| MoveJ | try_execute | `target: str` | `Union[Item, List[float], Mat]` | str → `RDK.Item(name)`，只传 Item 模式 |
| MoveL | try_execute | `target: str` | `Union[Item, List[float], Mat]` | str → `RDK.Item(name)`，只传 Item 模式 |
| Grasp | try_execute | `expected_item: str` | `robolink.Item` | str → `RDK.Item(name)` |
| Release | try_execute | `expected_item: str` | `robolink.Item` | str → `RDK.Item(name)` |

**不暴露给 LLM**：`ref_frame`（Mat，Item 模式不需要）、`blocking`（默认 True）、`tool`（使用 active tool）
