# CLAUDE.md — LLM-Driven Industrial Robot Assembly System

本文件是 Claude Code 的行为规范与项目约定。**每次开始任务前必须阅读本文件。**

---

## 项目一句话描述

接收自然语言装配指令，通过多智能体状态机驱动机器人完成工业装配；在能力边界处强制挂起交由人工介入，绝不幻觉。

---

## 语言

----
交流时输出中文，代码注释一律用英文

----

## 开发和规范原则

---
- 当发现原文档的设计有问题时或与你探讨出有问题时，应在许可后将发现更新至文档并修改原来的表述，但是不要直接替换以保留迭代过程。
- 在规划时思考隐藏的逻辑问题，包括但不限于造成逻辑死锁的设计、兜底过多导致过度静默处理错误都应避免。
- **禁止在 `SkiLib/` 生产代码中使用 `print()`**，一律通过 `SkiLib/log.py` 提供的 `get_logger(__name__)` 获取模块级 logger 输出。Logger 配置双 Handler（控制台 StreamHandler + 轮转文件 RotatingFileHandler），行为与 print 等价但支持级别过滤和持久化。Notebook 实验代码不受此约束。详见 Phase 5 实现计划。
- **完成实现后必须更新文档**：每次新增或修改 Primitive / Skill / 核心字段后，同步更新 `SkiLib/ARCHITECTURE.md` 对应章节（状态标记、字段说明、示例代码）以及本文件（`CLAUDE.md`）中的目录结构与已知风险条目。文档与代码必须保持一致，过期文档视同技术债务。
---


## 技术栈

| 组件 | 选型 |
|------|------|
| Agent 编排 | LangGraph (`StateGraph`) |
| LLM 基础设施 | LangChain Core |
| 大模型 | **Claude** 强能力验证模型 + **本地 LLM**（如 ChatOllama / Llama-3.1） |
| 机器人仿真 | RoboDK |
| 工艺知识库 | YAML 规范文件 |
| 语言 | Python 3.11+ |

---

## 目录结构

```
RoboSkiAgent/
├── CLAUDE.md
├── Agent/                          # 实验用 Notebook，不含生产代码
│   ├── langchain_rag.ipynb
│   └── LangGraph.ipynb
└── SkiLib/                         # 核心库
    ├── ARCHITECTURE.md
    ├── __init__.py
    ├── base.py                     # 核心抽象：BasePrimitive / BaseSkill / SkillResult
    ├── robotcontext.py             # 运行时单例：RobotContext / PrimitiveRegistry
    ├── main.py
    ├── RDK_Test.py
    ├── utils.py
    ├── doc/
    │   ├── DEV_NOTES_SkillRegistry.md
    │   ├── IK_SOLVER_USAGE.md
    │   └── IMPLEMENTATION_PLAN_SkillRegistry.md
    ├── examples/
    ├── primitives/
    │   ├── motion.py               # MoveJ (完整) / MoveL (完整)
    │   └── gripper.py              # Grasp (完整) / Release (完整)
    └── skills/
        └── pick_and_place.py
```

**Agent/ 与 SkiLib/ 的关系**：Agent Notebook 通过导入 SkiLib 调用技能库，本身不包含任何业务逻辑实现。

---

## 架构与各节点职责

系统采用 **Plan-and-Execute** 多智能体状态机，分两层：

### Layer 1 · 调研与规划层

**Supervisor**
- 在局部 ReAct 循环中调用 Task-skills，消除业务未知信息至"知识饱和"
- ❌ 禁止计算坐标 `(x, y, z)`
- ❌ 禁止调用任何底层硬件 API
- ✅ 世界里只有符号和 ID（如 `Target_A`、`Tool_Gripper`）

**Planner**
- 使用强制结构化输出生成 `todo_list` JSON 任务队列
- 生成后必须抹除调研阶段的对话记录，防止污染下层
- ❌ 禁止输出模糊描述，输出必须是合法 JSON

### Layer 2 · 执行与清理层

**Dispatcher**（纯代码，非 LLM）
- ~~`todo_list.pop(0)` 提取 `current_task` 写入 Global State~~ ← 已废弃：无条件 pop 导致任务在 halt/失败时永久丢失
- ~~`todo_list[0]` peek~~ ← 已废弃：peek 需要 `last_result` 作为隐式路由信号，语义耦合不清晰
- ✅ **填充空槽**：仅当 `current_task == {}` 时才 `pop(0)` 填入；槽已有任务时跳过不覆盖
- ❌ 禁止引入任何 LLM 推理，任务流转必须 100% 确定性

> [2026-03-13 更新] 新增 manual 任务路径：
- ✅ **manual 任务**：填入 `type="manual"` 的任务时，同时设 `halt_flag=True`、`halt_reason="MANUAL_TASK"`，由 `after_dispatcher` 条件边路由到 `human_intervention`，绕过 Executor

**Executor**
- 只关注当前 `current_task`，动态加载 P/R/E-skills 完成单步物理动作
- ✅ 执行结果写入 `last_result`（数据用途），Context Flush 据此决定成功/失败路径
- ❌ 禁止解析 YAML 规范
- ❌ 禁止思考业务逻辑（"为什么要抓这个零件"）

**Context Flush**（纯代码）
- ✅ **成功时**：清空 `current_task = {}`（腾出槽位），清空 `last_result = None`
- ✅ **失败时**：设置 `halt_flag = True`；`current_task` 与 `todo_list` 保持不变，resume 后直接重试同一任务
- 用 `RemoveMessage` 抹除 Executor 产生的 `ToolMessage` 噪音
- ❌ 绝不删除上层下发的 `current_task` 相关消息

> [2026-03-13 更新] 失败路径细化：
- ✅ **失败且 `last_result.needs_hilp=True`**：设 `halt_flag=True`，设 `halt_reason="TASK_FAILURE"`；`current_task/todo_list` 保持不变
- ✅ **失败且 `last_result.needs_hilp=False`**：理论上不应出现（Executor 未放弃时不应退出节点）；若出现，保守 fallthrough 至 halt，不允许静默跳过失败任务
- ✅ **成功时**：额外清空 `halt_reason=None`

**HumanIntervention**（LangGraph interrupt，新增节点）
- 接收两类入口：
  - `halt_reason="TASK_FAILURE"`：Executor 无法自愈，操作员选择 `retry` 或 `abort`
  - `halt_reason="MANUAL_TASK"`：计划内人工任务，操作员选择 `complete` 或 `abort`
- `retry`：清除 `halt_flag/halt_reason`，保留 `current_task` → Executor 重试同一任务
- `complete`：清除 `halt_flag/halt_reason`，清空 `current_task` → Dispatcher 推进到下一任务
- `abort`：清除 `halt_flag/halt_reason`，清空 `current_task + todo_list` → END
- ❌ `retry` 对 `MANUAL_TASK` 非法（会导致 Executor 找不到 skill 进入无限 HILP 循环），节点内强制降级为 `abort`

---

## Global State 结构

```python
class GlobalState(TypedDict):
    todo_list: list[dict]        # Planner 生成的任务队列；Dispatcher 成功后消费头部
    current_task: dict           # 执行槽：{} = 空闲，{...} = 执行中或失败保留
    robot_state: RobotState      # 当前机器人位姿、关节角、夹爪状态
    halt_flag: bool              # True = 系统已挂起，等待人工介入
    last_result: Optional[dict]  # Executor 写入的结果数据（非路由信号）
    execution_log: list[str]     # 极简状态上报，由 Context Flush 写入
    messages: list[BaseMessage]  # LangGraph 消息列表
```

> [2026-03-13 更新] 新增两个字段：
```python
class GlobalState(TypedDict):
    # ... 原有字段不变 ...
    halt_reason: Optional[str]   # "TASK_FAILURE" | "MANUAL_TASK" | None；HumanIntervention 节点读取以展示正确提示
    _hi_action:  Optional[str]   # 内部路由字段：human_intervention 写入，after_human_intervention 读取，不对外暴露
```

**todo_list 任务格式（新增 `type` 字段）：**
```python
# 自动任务（由 Executor 执行）
{"task_id": "t1", "type": "auto",   "skill": "PickAndPlace", "params": {...}}

# 人工任务（Dispatcher 直接路由到 human_intervention，不经过 Executor）
{"task_id": "t2", "type": "manual", "description": "手动拧紧 M10 螺栓至 25 N·m"}
```
Planner 可在同一 `todo_list` 中混排，Dispatcher 按 `type` 字段自动路由，完全确定性。

**`current_task` 作为执行槽的状态语义（单一真相来源）：**
- `{}` → 槽空闲，Dispatcher 负责填入下一个任务
- `{...}` → 任务在执行中，或失败后保留等待 resume 重试；Dispatcher 看到非空槽不会覆盖

**路由信号来源**：`halt_flag`（而非 `last_result`）— Context Flush 失败时设置 `halt_flag=True`，`should_continue` 只需检查 `halt_flag` 和 `todo_list`，无隐式字段耦合。

---

## SkillResult — 底层错误必须具身化

**禁止**将 Python `Exception` / traceback 直接传给 LLM。所有底层错误必须经 `SkillResult` 封装。

> **迁移说明**：`base.py` 中现有的 `CheckResult` 将被 `SkillResult` 取代。迁移完成前，新代码一律使用 `SkillResult`，不得新增 `CheckResult` 的使用。

```python
@dataclass
class SkillResult:
    success: bool
    execution_phase: ExecutionPhase   # PLANNING/MOVING/GRIPPING/RELEASING/...
    robot_state: RobotState           # 当前位姿快照
    error_type: Optional[str]         # "IK_FAILURE" / "COLLISION" / "TIMEOUT"
    suggestion: Optional[str]         # "尝试从上方接近" / "请求人工介入"
    data: Optional[dict]              # 技能返回的有效数据
```

> [2026-03-13 更新] 新增 `needs_hilp` 字段：
```python
@dataclass
class SkillResult:
    # ... 原有字段不变 ...
    needs_hilp: bool = True
    # True（默认）= Executor 已放弃，Context Flush 应触发 HILP
    # False       = Executor 内部 ReAct 仍在尝试恢复，此状态不应从 Executor 节点输出；
    #               若 Context Flush 意外收到 success=False + needs_hilp=False，
    #               保守处理为 needs_hilp=True（防止静默跳过失败任务）
```
**stub 阶段默认值 `True` 与现有行为完全一致**，不破坏当前测试。Phase 3 实现真实 Executor ReAct 后，内部循环在放弃时才设 `needs_hilp=True` 退出节点。

---

## `@require_robot_active` 装饰器规范

所有 R-skills（动作技能）必须使用此装饰器，`halt_flag=True` 时从底层锁死一切动作。

```python
# ✅ 正确
@require_robot_active
def move_to_target(self, target_id: str) -> SkillResult: ...

# ✅ 白名单：解除锁定的技能必须设 bypass_halt=True，否则造成死锁
@require_robot_active(bypass_halt=True)
def resume(self) -> SkillResult: ...

@require_robot_active(bypass_halt=True)
def request_human_intervention(self, reason: str) -> SkillResult: ...
```

**白名单必须包含**：`resume`、`request_human_intervention`。漏掉任何一个会导致系统永久卡死。

---

## SkiLib 核心抽象约定

### 两层基类

| 基类 | 文件 | 职责 |
|------|------|------|
| `BasePrimitive` | `base.py` | 底层动作原语，定义 `check() / execute() / try_execute()` 接口 |
| `BaseSkill` | `base.py` | 高层技能，通过 `REQUIRED_PRIMITIVES` 声明依赖，初始化时自动校验 |

### PrimitiveRegistry 初始化时机

`PrimitiveRegistry` 在启动时自动扫描 `primitives/` 模块并反射注册所有 `BasePrimitive` 子类。**LangGraph 节点初始化之前必须确保 Registry 已完成注册**，否则 Executor 首次调用会因找不到 primitive 而失败。

### 新增 Primitive 规范

1. 继承 `BasePrimitive`，放入 `primitives/` 对应模块
2. `check()` 必须实现完整（`MoveL.check()` 已实现，见 2026-03-13 更新）
3. `execute()` 内部异常全部捕获，返回 `SkillResult`，禁止向上抛出
4. 仿真/真机差异在 primitive 层屏蔽，上层不感知

### 新增 Skill 规范

1. 继承 `BaseSkill`，放入 `skills/` 目录
2. 在 `REQUIRED_PRIMITIVES` 中声明所有依赖
3. 返回类型必须是 `SkillResult`

### SkillRegistry 与 LLM Tool 生成

> [2026-03-13 新增]

**SkillRegistry**（对应 `PrimitiveRegistry`）在启动时自动扫描 `skills/` 目录，发现所有 `BaseSkill` 子类并用 primitives 注入实例化。

**Tool Schema 生成原则**：`BaseSkill` 提供 `as_tools()` 方法，通过反射将 `check / execute / try_execute` 三个方法自动包装为 `StructuredTool`，供 Executor ReAct 循环的 `llm.bind_tools()` 使用。

**底层机制**：Python 绑定方法（bound method）已捕获 `self`，LangChain 的 `StructuredTool.from_function` 通过 `inspect.signature` 读取类型注解自动生成 JSON Schema，LLM 只看到参数字典，不感知实例存在。

**Skill 方法签名约定**：
- `check / execute / try_execute` 三个方法**签名必须一致**，参数全部使用基础类型（`str`、`int`、`float`）表示符号 ID
- **符号解析**（`"Target_A"` → RoboDK Item）在方法体内通过 `RobotContext.instance()` 完成，上层不感知 Python 对象

```python
# ✅ 正确：参数为符号字符串，方法内解析
class PickAndPlace(BaseSkill):
    SKILL_DESCRIPTION = "Pick an object from pick_target and place it at place_target."

    def execute(self, pick_target: str, place_target: str, approach_height: int = 100) -> SkillResult:
        """Execute pick and place. pick_target/place_target are RoboDK target names."""
        ctx = RobotContext.instance()
        pick_item  = ctx.RDK.Item(pick_target)   # 符号 → Python 对象
        place_item = ctx.RDK.Item(place_target)
        ...

# ❌ 错误：参数为 Python 对象，LLM 无法序列化
def execute(self, pick_target: robolink.Item, ...) -> SkillResult: ...
```

**`as_tools()` 实现模式**（在 `BaseSkill` 中统一实现，子类无需重写）：

```python
def as_tools(self) -> List[StructuredTool]:
    skill_name = type(self).__name__
    tools = []
    for method_name in ("check", "execute", "try_execute"):
        method = getattr(self, method_name)   # bound method，self 已捕获
        @functools.wraps(method)
        def _wrapper(*args, _m=method, **kwargs):
            result = _m(*args, **kwargs)
            return result.to_llm_message() if isinstance(result, SkillResult) else result
        tools.append(StructuredTool.from_function(
            func=_wrapper,
            name=f"{skill_name}.{method_name}",
            description=method.__doc__ or f"{skill_name} {method_name}",
        ))
    return tools
```

**Executor 使用方式**：

```python
tools = skill_registry.get_tools()   # 所有 skill 的 as_tools() 展平
llm_with_tools = llm.bind_tools(tools)
# LLM 可调用：PickAndPlace.check / PickAndPlace.execute / PickAndPlace.try_execute
```

---

## 已知风险，开发时需特别注意

**MoveL.check() 已实现** *(2026-03-13)*
`primitives/motion.py` 中 `MoveL.check()` 通过 `MoveL_Test` 检查碰撞和全路径 IK 可解性（含奇点检测）。原空函数体说明已过期，此条目保留迭代记录。

**本地 LLM Structured Output 不稳定**
Planner 的 JSON 输出必须加 Schema 校验 + retry 逻辑。不要假设模型每次都输出合法 JSON。

**Context Flush 删除时机**
Executor ReAct 循环重试时，`RemoveMessage` 的 ID 范围必须精确。建议在 Executor 入口为 `current_task` 消息打标签，Context Flush 按标签保留，其余全删。

**YAML 规范歧义**
Supervisor 遇到规范描述模糊时，必须调用 `request_human_intervention`，禁止自行猜测工艺意图。

**RoboDK 仿真 → 真机切换**
仿真验证通过后才能切换真机。切换逻辑应在 `SkillBase` 层统一控制，Executor 不感知仿真/真机差异。

---

## 五条 Golden Rules

1. **大脑不碰坐标** — Supervisor/Planner 只处理符号和 ID，坐标全部在底层技能内计算
2. **手脚不看业务** — Executor 只接受参数指令，不解析 YAML，不理解"为什么"
3. **错误必须具身化** — 底层物理错误必须经 `SkillResult` 翻译，禁止裸露 traceback
4. **流转必须确定性** — 任务调度由 Dispatcher 纯代码掌控，LLM 只负责推理
5. **宁挂起不幻觉** — 遇到能力边界触发 HILP，禁止幻觉出不存在的工具或动作