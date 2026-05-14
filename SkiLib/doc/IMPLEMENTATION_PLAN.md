# RoboSkiAgent 实现计划

**Date**: 2026-03-11
**Status**: 设计完成，待实现

---

## 背景

项目是一个"大模型驱动的工业级机器人装配系统"，采用 Plan-and-Execute 多智能体状态机（LangGraph），将自然语言装配指令翻译为机器人物理动作。

**架构框架已完整**，但 LLM 节点全部是 stub，缺少：
- `@require_robot_active` 安全装饰器（全局锁）
- Supervisor / Planner / Executor 的真实 LLM 实现
- 外层控制循环（main.py）
- 技能注册系统（Skill Registry）
- 缺失的原语：MoveL.check()、Grasp、Release

当前 `graph.py` 的 Dispatcher + Context Flush 是完整的生产代码，可直接复用。

---

## 架构决策（设计探讨结论）

### 决策 1：串行执行模型

Agent 层采用**完全串行**的执行模型，不引入并发：

```
Executor 调用 skill.try_execute() → 同步阻塞 → 等机器人完成 → 返回 SkillResult
```

**理由**：
- Plan-and-Execute 架构下，Planner 已经决定了所有步骤，Executor 只需忠实执行
- 机器人是串行资源，物理上无法并发
- Agent 在执行期间没有任何推理需要，阻塞是正确的语义
- 无实时反馈需求（传感器反馈在 Primitive 层处理，不需要 Agent 层感知）

**结论**：不需要 Queue、不需要双线程，单进程同步调用完全成立。

### 决策 2：halt_flag 是任务间信号，不是运动中断信号

```
软件 halt_flag  → 控制"是否派发下一个任务"（任务边界检查）
硬件 E-stop     → 控制"是否中断当前运动"（机器人控制器负责）
```

`@require_robot_active` 在 Primitive 的 `execute()` **入口**检查 halt_flag，不是运动步骤之间。

**halt_flag 同步方式**：Executor 节点入口将 `GlobalState["halt_flag"]` 同步到 `RobotContext.halt_flag`，装饰器从 RobotContext 读取。

### 决策 3：RobotContext 生命周期 = Python 进程生命周期

```
进程启动
  └─ RobotContext() 初始化，连接 RoboDK    ← 全程只做一次

  ├─ invoke("将 Part_A 放入 Tray_1")       ← 内层循环（一次 LangGraph 调用）
  ├─ invoke("将 Part_B 拧紧到 Base")        ← 内层循环
  └─ invoke("回到 Home 位置")               ← 内层循环

进程退出时关闭连接
```

LangGraph `app.invoke()` 到达 END 只是**内层循环结束**，不影响 RobotContext 和 RoboDK 连接。

### 决策 4：两层循环结构

```
外层循环（main.py）          维持 RobotContext，接收用户指令，等待下一条
  └─ 内层循环（invoke）      一次完整的 Supervisor→Planner→Dispatcher×N→END
       └─ HITL（interrupt）  内层内部暂停，同一 thread_id 恢复，不退出到外层
```

`main.py` 是常驻进程，程序在每轮 agent 结束后继续等待指令，而不是退出。

### 决策 5：robot_state 每次 invoke 前从 RobotContext 读取

GlobalState 中的 `robot_state` 不跨 invoke 传递。每次新指令执行前，从 RobotContext 读取真实位姿初始化：

```python
def build_initial_state(context: RobotContext, instruction: str) -> GlobalState:
    return {
        "messages":      [HumanMessage(instruction)],
        "robot_state":   context.get_current_state(),  # 从 RoboDK 读真实状态
        "todo_list":     [],
        "current_task":  {},
        "halt_flag":     False,
        "last_result":   None,
        "execution_log": [],
    }
```

### 决策 6：HITL 使用 LangGraph interrupt() + MemorySaver

```python
# human_intervention 节点内
def human_intervention(state: GlobalState) -> dict:
    user_response = interrupt({           # 图在此暂停，LangGraph 序列化状态到 checkpointer
        "reason":       state["last_result"],
        "current_task": state["current_task"],
        "log":          state["execution_log"][-3:],
    })
    if user_response["action"] == "resume":
        return {"halt_flag": False}
    elif user_response["action"] == "abort":
        return {"halt_flag": False, "current_task": {}}

# 外层循环中恢复（同一 thread_id）
app.invoke(Command(resume={"action": "resume"}), config={"configurable": {"thread_id": tid}})
```

`create_graph()` 需要接受 `checkpointer` 参数（prototype 用 `MemorySaver`，生产用 `SqliteSaver`）。

---

## 实现分阶段路线

### Phase 0 · 安全基础设施（依赖前提）

**目标**：在任何 LLM 节点接入之前，先把安全兜底建好。

#### 0.1 `@require_robot_active` 装饰器
- **文件**：`SkiLib/base.py`
- 从 `RobotContext.instance().halt_flag` 读取（由 Executor 入口同步写入）
- `halt_flag=True` 时立即返回 `SkillResult(success=False, error_type=ERROR_ROBOT_INACTIVE)`
- 支持 `bypass_halt=True` 参数（白名单：`resume`、`request_human_intervention`）
- 检查点在 `execute()` **入口**，不在运动步骤之间

```python
@require_robot_active
def execute(self, ...) -> SkillResult: ...

@require_robot_active(bypass_halt=True)
def resume(self) -> SkillResult: ...
```

#### 0.2 Skill Registry（技能注册系统）
- **新建文件**：`SkiLib/registry.py` + `SkiLib/decorators.py`
- 参考 `doc/DEV_NOTES_SkillRegistry.md` 的设计
- `@skill(name, description, category, parameters)` 装饰器，import 时自动注册
- `SkillRegistry` 单例：`get_skill(name)`, `get_llm_tool_schemas()`, `list_skills()`
- `SkiLib/__init__.py` 用 `pkgutil` 自动 import `primitives/` 和 `skills/`，触发所有装饰器

---

### Phase 1 · 最小可运行系统

**目标**：硬编码 todo_list，完整跑通 Dispatcher → Executor → Context Flush → HITL 循环，main.py 持续等待。

#### 1.1 main.py 外层循环 + Checkpointer
- **文件**：`SkiLib/main.py`（重写）
- 初始化 `RobotContext()`（一次）
- `create_graph(checkpointer=MemorySaver())` 编译图
- `while True` 接收用户指令，构造 `initial_state`，`invoke()` 调用
- HITL 恢复：捕获 `GraphInterrupt`，提示操作员，`invoke(Command(resume=...))`

```python
def main():
    context = RobotContext()
    app     = create_graph(checkpointer=MemorySaver())

    while True:
        instruction = input("\n> ").strip()
        if not instruction:
            continue

        state  = build_initial_state(context, instruction)
        thread = {"configurable": {"thread_id": str(uuid.uuid4())}}

        try:
            result = app.invoke(state, config=thread)
        except GraphInterrupt as hi:
            action = input(f"[HALT] {hi.value}\nresume / abort > ").strip()
            result = app.invoke(Command(resume={"action": action}), config=thread)

        print(f"[done] {result['execution_log'][-1]}")
```

#### 1.2 Executor 节点（直连 SkillRegistry）
- **文件**：`SkiLib/graph.py`
- 入口同步：`RobotContext.instance().halt_flag = state["halt_flag"]`
- `SkillRegistry.get_skill(task["skill"])` 查找技能实例
- `skill.try_execute(**task["params"])` 同步调用（阻塞直到完成）
- 返回 `last_result`（`SkillResult.to_llm_message()` 的 dict）

#### 1.3 human_intervention 节点 + 图路由修改
- **文件**：`SkiLib/graph.py`
- 新增 `human_intervention(state)` 节点（含 `interrupt()`）
- `should_continue` 路由改为 `"halt"` → `"human_intervention"`（不再直接 END）
- `create_graph(checkpointer=None)` 接受 checkpointer 参数并传入 `builder.compile()`

#### 1.4 MoveL.check() 补全
- **文件**：`SkiLib/primitives/motion.py:134`
- 参考同文件 `MoveJ.check()` 实现，调用 `MoveL_Test()` 进行碰撞检测
- 返回 `SkillResult(execution_phase=ExecutionPhase.PLANNING, ...)`

**验证**：`python SkiLib/main.py`，输入任意指令 → 硬编码 todo_list 在 RoboDK 仿真中真实执行 → 触发失败时 HITL 暂停 → 输入 resume 后继续 → 输入新指令程序继续等待。

---

### Phase 2 · Planner（结构化输出）

**目标**：用 Claude structured output 替换 Planner 的硬编码 todo_list。

#### 2.1 Pydantic Schema
- **新建文件**：`SkiLib/schemas.py`
- `TaskItem(BaseModel)`: `task_id: str, skill: str, params: dict, description: str`
- `TodoList(BaseModel)`: `tasks: List[TaskItem]`（含 JSON Schema 字段约束）

#### 2.2 Planner 节点
- **文件**：`SkiLib/graph.py`
- `ChatAnthropic(model="claude-opus-4-6").with_structured_output(TodoList)`
- System prompt：只生成符号/ID，禁止生成坐标，`skill` 字段必须是已注册的 skill 名称
- Pydantic 校验 + retry 最多 3 次
- 完成后用 `RemoveMessage` 抹除规划阶段消息（防止污染执行层）

**验证**：输入 `"将 Part_A 放入 Tray_1"` → Planner 输出合法 JSON todo_list → Executor 执行。

---

### Phase 3 · Supervisor（ReAct 调研循环）

**目标**：Supervisor 调用 Task-skills 消除业务未知信息，RemoveMessage 保持消息总线干净。

#### 3.1 Task-skills
- **新建文件**：`SkiLib/skills/task_skills.py`
- `read_yaml_specs(spec_file: str) -> dict`：读取 `specs/` 目录下 YAML 工艺规范
- `query_robodk_tree(filter: str = "") -> list`：从 `RobotContext` 列出 RoboDK 场景树 Item 名称/ID
- `request_human_intervention(reason: str)`：设置 halt_flag，带 `bypass_halt=True`
- 均包装为 LangChain `@tool`

#### 3.2 YAML 工艺规范示例
- **新建**：`SkiLib/scenes/fmb/assembly.yaml`
- 定义目标名称映射、工具 ID、工艺步骤约束、安全边界

#### 3.3 Supervisor 节点
- **文件**：`SkiLib/graph.py`
- `create_react_agent(llm, tools=[read_yaml_specs, query_robodk_tree, request_human_intervention])`
- System prompt：禁止计算坐标、只输出符号 ID、遇到规范歧义必须调用 `request_human_intervention`

#### 3.4 RemoveMessage 清理
- Planner 完成后清除 Supervisor 阶段的 ReAct 消息（`additional_kwargs={"source": "supervisor"}`）
- Context Flush 清除 Executor 层消息（`additional_kwargs={"source": "executor"}`）

**验证**：输入模糊指令 → Supervisor 查询 RoboDK 树 → 返回正确 Part ID → Planner 生成计划 → 执行。

---

### Phase 4 · 缺失原语补全

#### 4.1 Grasp / Release 原语
- **新建文件**：`SkiLib/primitives/gripper.py`
- `Grasp(BasePrimitive)`: `check()` 验证夹爪状态，`execute()` 调用 RoboDK Gripper API
- `Release(BasePrimitive)`: 同上
- 均使用 `@require_robot_active` 装饰 `execute()`，均注册 `@skill`

#### 4.2 PickAndPlace.execute() 补全
- **文件**：`SkiLib/skills/pick_and_place.py`
- 依赖：`MoveJ`, `MoveL`, `Grasp`, `Release`（已在 `REQUIRED_PRIMITIVES` 中声明）
- 完整序列：
  ```
  MoveJ(approach_pick) → MoveL(pick) → Grasp
    → MoveL(lift) → MoveJ(approach_place) → MoveL(place) → Release → MoveL(lift)
  ```
- approach pose 计算（目标位姿 + 安全偏移向量）在 skill 内部完成，不暴露给上层

---

## 关键文件一览

| 文件 | 状态 | 主要修改内容 |
|------|------|------------|
| `SkiLib/base.py` | 修改 | 新增 `@require_robot_active` 装饰器 |
| `SkiLib/main.py` | 重写 | 外层循环 + RobotContext 保活 + HITL 交互 |
| `SkiLib/graph.py` | 修改 | 替换所有 stub 节点；接受 checkpointer；新增 human_intervention |
| `SkiLib/registry.py` | **新建** | SkillRegistry 单例 |
| `SkiLib/decorators.py` | **新建** | `@skill` 装饰器 |
| `SkiLib/schemas.py` | **新建** | TodoList / TaskItem Pydantic 模型 |
| `SkiLib/__init__.py` | 修改 | pkgutil 自动发现 primitives/ + skills/ |
| `SkiLib/robotcontext.py` | 修改 | 新增 `halt_flag: bool` 字段 + `get_current_state()` 方法 |
| `SkiLib/primitives/motion.py` | 修改 | 补全 MoveL.check() |
| `SkiLib/primitives/gripper.py` | **新建** | Grasp / Release |
| `SkiLib/skills/pick_and_place.py` | 修改 | 补全 execute() |
| `SkiLib/skills/task_skills.py` | **新建** | Task-skills + request_human_intervention |
| `SkiLib/scenes/fmb/assembly.yaml` | **新建** | 示例工艺规范 |

## 可复用的现有实现

- `SkiLib/base.py`: `SkillResult`, `ExecutionPhase`, `RobotState`, `BasePrimitive`, `BaseSkill` — 不改动接口
- `SkiLib/robotcontext.py`: `RobotContext`, `PrimitiveRegistry` 单例 — 新增字段，不改现有接口
- `SkiLib/utils.py`: `IKSolver` — MoveL.check() 中复用
- `SkiLib/graph.py` 中的 `dispatcher()` 和 `context_flush()` — 逻辑不变，只补充 RemoveMessage
- `SkiLib/primitives/motion.py` 中的 `MoveJ` — MoveL.check() 的完整参考实现

## 实现顺序依赖

```
Phase 0.1 (@require_robot_active)
    ↓
Phase 0.2 (SkillRegistry + auto-discovery)
    ↓
Phase 1.1 (main.py 外层循环 + checkpointer)
Phase 1.2 (Executor 直连 Registry)
Phase 1.3 (human_intervention + interrupt + 路由修改)
Phase 1.4 (MoveL.check)
    ↓
Phase 2 (Planner 结构化输出)        Phase 4 (Grasp/Release, PickAndPlace)
    ↓                                    ↓
Phase 3 (Supervisor + RemoveMessage) ←───
```

## 验证方式（端到端）

1. **Phase 0-1**：`python SkiLib/main.py` → 输入指令 → 硬编码任务在 RoboDK 仿真中执行 → 触发失败 → HITL 暂停 → 输入 resume → 继续 → 程序等待下一条指令
2. **Phase 2**：输入 `"将 Part_A 放入 Tray_1"` → Planner 生成合法 JSON → Executor 执行
3. **Phase 3**：输入模糊指令 → Supervisor 查询 RoboDK 树 → 消歧义 → Planner 生成计划
4. **Phase 4**：完整 PickAndPlace 在 RoboDK 仿真中抓取并放置零件
5. **全程**：`LangGraph Studio`（`langgraph.json` 已配置）可视化节点状态和消息流
