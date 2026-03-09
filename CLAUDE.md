# CLAUDE.md — LLM-Driven Industrial Robot Assembly System

本文件是 Claude Code 的行为规范与项目约定。**每次开始任务前必须阅读本文件。**

---

## 项目一句话描述

接收自然语言装配指令，通过多智能体状态机驱动机器人完成工业装配；在能力边界处强制挂起交由人工介入，绝不幻觉。

---

## 语言

----
交流时输出中文，代码注释一律用英文
---

## 技术栈

| 组件 | 选型 |
|------|------|
| Agent 编排 | LangGraph (`StateGraph`) |
| LLM 基础设施 | LangChain Core |
| 大模型 | 本地 LLM，ChatOllama / Llama-3.1 |
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
    │   └── motion.py               # MoveJ (完整) / MoveL (execute 完整, check 待实现)
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
- `todo_list.pop(0)` 提取 `current_task` 写入 Global State
- ❌ 禁止引入任何 LLM 推理，任务流转必须 100% 确定性

**Executor**
- 只关注当前 `current_task`，动态加载 P/R/E-skills 完成单步物理动作
- ❌ 禁止解析 YAML 规范
- ❌ 禁止思考业务逻辑（"为什么要抓这个零件"）

**Context Flush**（纯代码）
- 用 `RemoveMessage` 抹除 Executor 产生的 `ToolMessage` 噪音
- ✅ 只删底层噪音
- ❌ 绝不删除上层下发的 `current_task` 相关消息

---

## Global State 结构

```python
class GlobalState(TypedDict):
    todo_list: list[dict]        # Planner 生成的任务队列
    current_task: dict           # Dispatcher 当前下发的单步任务
    robot_state: RobotState      # 当前机器人位姿、关节角、夹爪状态
    halt_flag: bool              # True = 系统已挂起，等待人工介入
    execution_log: list[str]     # 极简状态上报，由 Context Flush 写入
    messages: list[BaseMessage]  # LangGraph 消息列表
```

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
2. `check()` 必须实现完整（`MoveL.check()` 当前待完成，是已知 TODO）
3. `execute()` 内部异常全部捕获，返回 `SkillResult`，禁止向上抛出
4. 仿真/真机差异在 primitive 层屏蔽，上层不感知

### 新增 Skill 规范

1. 继承 `BaseSkill`，放入 `skills/` 目录
2. 在 `REQUIRED_PRIMITIVES` 中声明所有依赖
3. 返回类型必须是 `SkillResult`

---

## 已知风险，开发时需特别注意

**MoveL.check() 尚未实现**
`primitives/motion.py` 中 `MoveL.check()` 是空函数体，当前所有直线运动跳过 pre-flight 校验直接执行。补全前不得在生产流程中依赖 MoveL 的检查结果。

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