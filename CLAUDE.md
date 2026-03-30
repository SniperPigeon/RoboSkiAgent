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
- **禁止在 `SkiLib/` 生产代码中使用 `print()`**，一律通过 `SkiLib/log.py` 提供的 `get_logger(__name__)` 获取模块级 logger 输出。Logger 配置双 Handler（控制台 StreamHandler + 轮转文件 RotatingFileHandler），行为与 print 等价但支持级别过滤和持久化。Notebook 实验代码不受此约束。`SkiLib/log.py` 已实现（2026-03-17）。
- **实现完毕的功能必须同步更新文档**，包括 CLAUDE.md 目录结构、IMPLEMENTATION_CHECKLIST.md 对应条目、以及 ARCHITECTURE.md 中的状态标注。未更新文档的实现视为不完整。
- **开发前必须激活虚拟环境**：运行 Notebook、执行脚本、调试 SkiLib 或启动 Agent 前，须先激活项目虚拟环境（如 `conda activate <env>` 或 `.venv\Scripts\activate`）。未激活环境直接运行可能导致依赖缺失或版本冲突，且错误现象不易排查。
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

> 最后更新：2026-03-30（Phase 6 迁移完成，Agent/ 包已生产化）

```
RoboSkiAgent/
├── CLAUDE.md
├── IMPLEMENTATION_CHECKLIST.md
├── Agent/                          # Agent 编排层（生产代码）；依赖 SkiLib，单向依赖
│   ├── __init__.py                 # 重新导出 build_graph / make_initial_state / GlobalState
│   ├── state.py                    # GlobalState TypedDict（todo_list / current_task / halt_flag / ...）
│   ├── llm.py                      # LLM 工厂：ROBOSKI_LLM_PROVIDER=claude（默认）/ ollama
│   ├── graph.py                    # build_graph() / make_initial_state()；含 MemorySaver + JsonPlusSerializer
│   ├── gui.py                      # Gradio GUI：完整 interrupt 处理（plan_review / hitl / manual）✅
│   ├── __main__.py                 # CLI 入口：python -m Agent "<指令>"
│   │                               #   ⚠️ 使用 graph.invoke()，遇 interrupt 节点会抛 NodeInterrupt
│   │                               #   含人工审批的完整流程请改用 GUI
│   ├── prompts/                    # 提示词模板（纯文本，运行时 .format() 注入）
│   │   ├── supervisor.txt
│   │   ├── planner.txt
│   │   └── executor.txt
│   ├── nodes/                      # 各节点独立模块
│   │   ├── __init__.py
│   │   ├── supervisor.py           # T-skills 查询 + SupervisorOutput 结构化输出
│   │   ├── planner.py              # 动态生成 add_<Skill>_task 工具，LLM tool-call 构建 todo_list
│   │   ├── plan_review.py          # interrupt 审批门（approve / replan / abort）
│   │   ├── dispatcher.py           # 纯代码槽位填充；manual 任务设 halt_flag
│   │   ├── executor.py             # try_execute + LLM 恢复循环 + _EscalateHITLException 升级
│   │   ├── manual_handler.py       # interrupt：complete / abort
│   │   └── hitl_handler.py         # interrupt：retry / next_task / replan / abort
│   └── notebooks/                  # 历史实验 Notebook（参考用，核心逻辑已迁移）
│       ├── langchain_rag.ipynb     # RAG 实验
│       ├── LangGraph.ipynb         # 早期图流转探索（已过期）
│       └── graph_test.ipynb        # 原始实现参考（2026-03-27，已被 Agent/ 包取代）
└── SkiLib/                         # 纯技能库（无 LangGraph 依赖，可独立测试）
    ├── ARCHITECTURE.md
    ├── __init__.py
    ├── base.py                     # 核心抽象：BasePrimitive / BaseSkill / SkillResult / as_tools() / TOOL_METHODS
    ├── robotcontext.py             # 运行时单例：RobotContext / PrimitiveRegistry
    ├── registry.py                 # SkillRegistry 单例：反射扫描 skills/，实例化 BaseSkill，暴露 get_tools()
    ├── log.py                      # Logger 工厂：get_logger(__name__)，双 Handler（控制台 + 轮转文件）
    ├── main.py                     # 技能库调试入口（非生产，独立于 Agent 编排）
    ├── graph.py                    # ⚠️ 错位文件：含 LangGraph 依赖，违反 SkiLib 无 LangGraph 约束；
    │                               #   待迁移至 Agent/notebooks/ 或正式采纳后移入 Agent/graph.py
    ├── RDK_Test.py
    ├── utils.py
    ├── metatools/                  # T-skills：Supervisor 使用的只读场景查询工具（无坐标，符号名）
    │   ├── __init__.py
    │   └── informative.py          # list_targets / list_objects / list_tools / check_item_exists / get_gripper_state
    ├── doc/
    │   ├── DEV_NOTES_SkillRegistry.md
    │   ├── IK_SOLVER_USAGE.md
    │   └── IMPLEMENTATION_PLAN_SkillRegistry.md
    ├── primitives/
    │   ├── motion.py               # MoveJ (完整) / MoveL (完整，含 check())
    │   └── gripper.py              # Grasp (完整，仿真) / Release (完整，仿真)；参数 expected_item
    └── skills/
        ├── pick_and_place.py       # PickAndPlace (完整)：8步序列，显式接近点，initial_motion/transit_motion（均默认 MoveL）
        └── dummy_skills.py         # 测试桩（非生产）
```

**Agent/ 与 SkiLib/ 的关系**：`Agent/` 是编排层，通过 `from SkiLib.xxx import ...` 调用技能库。依赖方向单向：`Agent → SkiLib`，SkiLib 本身不依赖 LangGraph。

---

## 架构与各节点职责

系统采用 **Plan-and-Execute** 多智能体状态机，分两层：

### Layer 1 · 调研与规划层

**Supervisor**
- 在局部 ReAct 循环中调用 Task-skills，消除业务未知信息至"知识饱和"
- ❌ 禁止计算坐标 `(x, y, z)`
- ❌ 禁止调用任何底层硬件 API
- ✅ 世界里只有符号和 ID（如 `Target_A`、`Tool_Gripper`）

> [2026-03-26 实现] `graph_test.ipynb` 中已用 `create_agent` + `SupervisorOutput` 结构化 schema 实现真实 LLM 调用。工具集来自 `SkiLib/metatools/informative.py`（T-skills）。可用技能列表由代码注入 system prompt，LLM 不填写。

**Planner**
- ~~使用强制结构化输出生成 `todo_list` JSON 任务队列~~ ← 原设计
- ✅ **实际采用**（2026-03-26）：工具调用方式 — 为每个已注册 Skill 动态生成 `add_<SkillName>_task` 工具（复用 `try_execute` 的 args_schema），LLM 通过逐一调用工具构建计划，无需直接输出 JSON
- 优势：LLM 只需会用工具，不需要记住 JSON schema；参数校验由 Pydantic args_schema 自动完成
- 生成后必须抹除调研阶段的对话记录，防止污染下层（⚠️ 消息清理尚未实现）
- ❌ 禁止输出模糊描述，参数必须合法
- ~~system prompt 要求 LLM 在每个 plan 前插入一个 manual task 让操作员审批计划~~ ← 已废弃（2026-03-27）：prompt 层约束对弱模型不可靠，且操作员无法通过 manual task 纠正计划内容；审批职责移交给结构性节点 `plan_review`，`manual` 任务语义还原为"机器人做不了的人工步骤"（如手动拧紧螺栓）

### Layer 2 · 执行与清理层

**PlanReview**（LangGraph interrupt，计划审批门）
- 唯一入口：Planner 完成后，Dispatcher 启动前；图结构保证每次规划后必经此节点
- 向操作员展示完整 `todo_list` 摘要（task_id / type / skill 或 description）
- 操作员 actions：`approve` / `abort` / `replan`
  - `approve`：直接进 Dispatcher 开始执行；清 `halt_flag/halt_reason`
  - `abort`：清空 `todo_list`，进 END
  - `replan`：携带操作员修改意见（`{"action": "replan", "feedback": "..."}`）写入 `HumanMessage`，回到 `supervisor` 重新规划
- ✅ **结构保证**：审批由节点强制触发，与 LLM 能力无关；弱模型不会跳过审批
- ✅ **可纠错**：`replan` 路径让操作员把修改意见送回 supervisor，比 abort + 重启更高效

> [2026-03-27 实现] `graph_test.ipynb` 中已实现：
> - interrupt 展示完整 `todo_list` 摘要（task_id / type / skill 或 description）
> - `isinstance(result, dict)` 解包：approve/abort 传纯字符串，replan 传 `{"action": "replan", "feedback": "..."}`
> - replan 路径：写入 `HumanMessage` + 清空 `todo_list`，回 supervisor 重规划
> - abort 路径：清空 `todo_list`，路由到 END
> - `plan_review_action` 字段已加入 `GlobalState`
> - ⚠️ Planner system prompt 仍残留"在计划前插入 manual task"规则，会导致 approve 后弹出多余的 complete/abort，待删除该条规则
> GUI（2026-03-27）：`feedback_box` 改为常驻显示（避免 Gradio streaming 与 visible 更新的时序冲突）；`handle_choice` 当 `choice == "replan"` 时将文本打包为 `{"action": "replan", "feedback": <text>}` 传给 `Command(resume=...)`；`_check_for_interrupt` 修复 `IndexError`（API 异常时 `state.next` 非空但 `interrupts` 为空 tuple）。

**Dispatcher**（纯代码，非 LLM）
- ~~`todo_list.pop(0)` 提取 `current_task` 写入 Global State~~ ← 已废弃：无条件 pop 导致任务在 halt/失败时永久丢失
- ~~`todo_list[0]` peek~~ ← 已废弃：peek 需要 `last_result` 作为隐式路由信号，语义耦合不清晰
- ✅ **填充空槽**：仅当 `current_task == {}` 时才 `pop(0)` 填入；槽已有任务时跳过不覆盖
- ❌ 禁止引入任何 LLM 推理，任务流转必须 100% 确定性

> [2026-03-13 更新] 新增 manual 任务路径：
- ✅ **manual 任务**：填入 `type="manual"` 的任务时，同时设 `halt_flag=True`、`halt_reason="MANUAL_TASK"`，由 `after_dispatcher` 条件边路由到 `human_intervention`，绕过 Executor

> [2026-03-25 更新] `human_intervention` 已拆分为两个节点，manual 路径目标节点改为 `manual_task_handler`：
- ✅ **manual 任务**：填入后由 `after_dispatcher` 路由到 `manual_task_handler`（不再是 `human_intervention`）

> [2026-03-26 更新] `graph_test.ipynb` 框架重构，Dispatcher 路由条件函数改名为 `task_router`（返回 `"auto"` / `"manual"` / `"END"`）；节点命名调整：
- `manual_task_handler` → `manual_intervention_handler`
- `task_failure_handler` → `hitl_handler`（新增 `replan` 路径，可回到 Supervisor 重规划）

**Executor**
- 只关注当前 `current_task`，动态加载 P/R/E-skills 完成单步物理动作
- ✅ 执行结果写入 `last_result`（数据用途），Context Flush 据此决定成功/失败路径
- ❌ 禁止解析 YAML 规范
- ❌ 禁止思考业务逻辑（"为什么要抓这个零件"）

> [2026-03-26 更新] `graph_test.ipynb` Executor 节点完整实现：
- 直接调用 `skill_registry[skill_name].try_execute(**params)` 执行任务
- 失败时启动 LLM 恢复循环（ReAct 内循环，尝试自愈；循环内 `needs_hilp=False`）
- 放弃时抛出 `_EscalateHITLException(error_type, reason, suggestion)`，携带结构化失败诊断
- 所有路径均将 `SkillResult` 对象写入 `last_result`（类型已由 `Optional[dict]` 升为 `Optional[SkillResult]`）

**Context Flush**（纯代码）
- ✅ **成功时**：清空 `current_task = {}`（腾出槽位），清空 `last_result = None`
- ✅ **失败时**：设置 `halt_flag = True`；`current_task` 与 `todo_list` 保持不变，resume 后直接重试同一任务
- 用 `RemoveMessage` 抹除 Executor 产生的 `ToolMessage` 噪音
- ❌ 绝不删除上层下发的 `current_task` 相关消息

> [2026-03-13 更新] 失败路径细化：
- ✅ **失败且 `last_result.needs_hilp=True`**：设 `halt_flag=True`，设 `halt_reason="TASK_FAILURE"`；`current_task/todo_list` 保持不变
- ✅ **失败且 `last_result.needs_hilp=False`**：理论上不应出现（Executor 未放弃时不应退出节点）；若出现，保守 fallthrough 至 halt，不允许静默跳过失败任务
- ✅ **成功时**：额外清空 `halt_reason=None`

> [2026-03-26 更新] `graph_test.ipynb` 中 Context Flush **不作为独立节点**，其路由逻辑合并为 `executor` 的条件出边函数 `post_task_router`（返回 `"dispatcher"` / `"hitl_handler"` / `"END"`）。消息清理（`RemoveMessage`）尚未实现，待 Phase 4 补充。

~~**HumanIntervention**（LangGraph interrupt，新增节点）~~
~~- 接收两类入口：~~
~~  - `halt_reason="TASK_FAILURE"`：Executor 无法自愈，操作员选择 `retry` 或 `abort`~~
~~  - `halt_reason="MANUAL_TASK"`：计划内人工任务，操作员选择 `complete` 或 `abort`~~
~~- `retry`：清除 `halt_flag/halt_reason`，保留 `current_task` → Executor 重试同一任务~~
~~- `complete`：清除 `halt_flag/halt_reason`，清空 `current_task` → Dispatcher 推进到下一任务~~
~~- `abort`：清除 `halt_flag/halt_reason`，清空 `current_task + todo_list` → END~~
~~- ❌ `retry` 对 `MANUAL_TASK` 非法（会导致 Executor 找不到 skill 进入无限 HITL 循环），节点内强制降级为 `abort`~~

> [2026-03-25 更新] 单节点设计有缺陷：两种入口的合法 actions 不同，靠运行时 guard 防御非法组合（`retry` on `MANUAL_TASK`）是设计异味。拆分为两个独立节点，非法组合从结构上消失。

**ManualTaskHandler**（LangGraph interrupt，计划内人工步骤）
- 唯一入口：`after_dispatcher` 路由 `type="manual"` 任务
- 操作员 actions：`complete` / `abort`（不提供 `retry`，结构上排除非法组合）
- `complete`：清除 `halt_flag/halt_reason`，清空 `current_task` → Dispatcher 推进到下一任务
- `abort`：清除 `halt_flag/halt_reason`，清空 `current_task + todo_list` → END

> [2026-03-26 修正] `graph_test.ipynb` 实现细节修正：
- `current_task` 清空值修正为 `{}` （原误写为 `None`，与执行槽语义不符）
- `halt_flag=False` / `halt_reason=None` 两处字段清零均已补齐

**TaskFailureHandler**（LangGraph interrupt，执行故障恢复）
- 唯一入口：`context_flush` 失败路径（`needs_hilp=True`）
- 操作员 actions：`retry` / `abort`（不提供 `complete`，语义上不合理）
- `retry`：清除 `halt_flag/halt_reason`，保留 `current_task` → Executor 重试同一任务
- `abort`：清除 `halt_flag/halt_reason`，清空 `current_task + todo_list` → END

> [2026-03-26 修正] `graph_test.ipynb` hitl_handler 实现细节修正：
- 节点入口打印 `last_result` 诊断信息（`error_type`、`suggestion`）
- **所有路径**（`retry` / `next_task` / `replan` / `abort`）均显式清 `halt_flag=False`；原部分路径遗漏
- `next_task` / `abort` 路径中 `current_task` 清空值修正为 `{}` （原误写为 `None`）
- `replan` 路径同样清 `halt_flag/halt_reason`，清空 `current_task`，回到 `supervisor`

---

## Global State 结构

```python
class GlobalState(TypedDict):
    todo_list: list[dict]        # Planner 生成的任务队列；Dispatcher 成功后消费头部
    current_task: dict           # 执行槽：{} = 空闲，{...} = 执行中或失败保留
    robot_state: RobotState      # 当前机器人位姿、关节角、夹爪状态
    halt_flag: bool              # True = 系统已挂起，等待人工介入
    last_result: Optional[SkillResult]  # Executor 写入的结果数据（非路由信号）；原 Optional[dict]，2026-03-26 升为 SkillResult
    execution_log: list[str]     # 极简状态上报，由 Context Flush 写入
    messages: list[BaseMessage]  # LangGraph 消息列表
```

> [2026-03-26 设计决策] **messages / execution_log 职责分离**（Gradio 接入 + HITL interrupt 前置条件）
> - **`execution_log`**：唯一展示渠道。所有节点（supervisor / planner / executor / hitl_handler / manual_intervention_handler）均写入，格式统一 `"[节点名] 内容"`；不参与 LLM 推理，Gradio 订阅此字段。
> - **`messages`**：职责收窄为 LLM 推理链。supervisor 读取上下文；各节点禁止向此字段追加状态摘要 AIMessage（否则 LLM 每轮都会看到越来越多的执行噪音）。
> - **例外**：hitl_handler `replan` 路径写入 `HumanMessage` 触发 supervisor 重规划——这是 LLM 推理输入，不是展示日志，保留此行为。
> - **待实现**：`graph_test.ipynb` 中 executor/planner 双写 messages 的冗余 AIMessage 待删除（见 Checklist 3.5.7）。

> [2026-03-13 更新] 新增两个字段：
```python
class GlobalState(TypedDict):
    # ... 原有字段不变 ...
    halt_reason: Optional[str]   # "TASK_FAILURE" | "MANUAL_TASK" | None；诊断用，节点拆分后不再作路由信号
    _hi_action:  Optional[str]   # 内部路由字段：manual_task_handler / task_failure_handler 写入，
                                 #   after_manual_task / after_task_failure 读取，不对外暴露
```

> [2026-03-26 更新] `graph_test.ipynb` 框架重构，`_hi_action` 被两个独立字段替代：
```python
class GlobalState(TypedDict):
    # ... 原有字段不变（含 halt_reason）...
    intervention_action: Optional[str]  # manual_intervention_handler 写入："complete" | "abort"
                                        # manual_intervention_router 读取；不跨节点使用
    hitl_command: Optional[str]         # hitl_handler 写入："retry" | "next_task" | "replan" | "abort"
                                        # hitl_router 读取；不跨节点使用
```
> `replan` 是新增路径：hitl_handler 可发 `"replan"` → 路由回 `supervisor` 重新规划整个序列（原 CLAUDE.md 未包含此选项）

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
    # True（默认）= Executor 已放弃，Context Flush 应触发 HITL
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
5. **宁挂起不幻觉** — 遇到能力边界触发 HITL，禁止幻觉出不存在的工具或动作