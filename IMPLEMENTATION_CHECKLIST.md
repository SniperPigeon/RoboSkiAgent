# RoboSkiAgent 实现 Checklist

> 按依赖顺序排列。每阶段完成后再进入下一阶段。
> 最后更新：2026-03-13

---

## Phase 0 · SkillRegistry 基础设施
*所有后续阶段的前置依赖*

- [x] **0.1** 新建 `SkiLib/registry.py`：`SkillRegistry` 单例 *(2026-03-18)*
  - ~~`register(skill_class, metadata)` — 由 `@skill` 装饰器调用~~ → 改为反射扫描（见 0.2 决策说明）
  - `set_robot_context(context)` — 触发 eager init，创建所有 Skill 实例
  - `get_skill(name)` / `registry[name]` — 返回已初始化实例
  - `list_skills(category=None)` — 列举已注册技能（供 Supervisor 查询）
  - `get_llm_tool_schemas(format)` — 生成 Anthropic 格式工具 schema（基于 LangChain args_schema）
  - `get_tools()` — 展平所有 skill.as_tools()，供 Executor llm.bind_tools() 使用

- [x] **0.2** ~~新建 `SkiLib/decorators.py`~~ **决策：不创建** *(2026-03-18)*
  - **原计划**：`@skill` 装饰器在 import 时注册元数据
  - **实际采用**：反射扫描（镜像 PrimitiveRegistry），元数据用类变量 `SKILL_DESCRIPTION / SKILL_CATEGORY` 声明
  - **理由**：与 PrimitiveRegistry 完全一致，无循环导入风险，零样板代码
  - `BaseSkill` 新增 `SKILL_DESCRIPTION: str = ""` 和 `SKILL_CATEGORY: str = "skill"` 类变量
  - `BaseSkill` 新增 `as_tools() -> List[StructuredTool]`（按 CLAUDE.md 规范，lazy import LangChain）

- [x] **0.3** ~~更新 `SkiLib/__init__.py`~~ **无需改动** *(2026-03-18)*
  - SkillRegistry 在 `set_robot_context()` 时自己扫描 `skills/`，不依赖 `__init__.py` 预先 import

- [x] **0.4** 更新 `SkiLib/robotcontext.py` *(2026-03-18)*
  - `RobotContext.__init__` 末尾调用 `SkillRegistry().set_robot_context(self)`（local import 避免循环）
  - 新增 `get_current_state() -> RobotState`（关节角 + 位姿快照，供 GlobalState 初始化）
  - **技术债务保留**：`PrimitiveRegistry` 中 `from SkiLib.primitives import motion` 死代码未清理（Phase 5 统一处理）

---

## Phase 1 · RoboDK 场景查询层
*Supervisor 消歧义的数据来源；Agent 层所有"符号"从这里取*

- [ ] **1.1** 新建 `SkiLib/primitives/scene_query.py`（底层 RoboDK API 封装）
  - `ListItems(item_type) -> SkillResult`：调用 `RDK.ItemList(item_type)`，返回 `data={"items": [{name, type}]}`
  - `GetTargetPose(target_name) -> SkillResult`：`RDK.Item(name, ITEM_TYPE_TARGET).Pose()`，返回位姿矩阵
  - `GetApproachTarget(target_name) -> SkillResult`：按命名约定 `"Approach_<target_name>"` 查找接近点；不存在时 `success=False, error_type="NO_APPROACH_TARGET"`
  - `CheckTargetReachable(target_name) -> SkillResult`：调用 `MoveJ_Test` 验证可达性，不移动机器人
  - 所有方法使用 `@skill(category="query")` 装饰器注册

- [ ] **1.2** 新建 `SkiLib/skills/task_skills.py`（Supervisor ReAct 工具集）
  - `list_available_targets() -> SkillResult`：封装 `ListItems(ITEM_TYPE_TARGET)`
  - `list_available_tools() -> SkillResult`：封装 `ListItems(ITEM_TYPE_TOOL)`
  - `get_target_info(target_name) -> SkillResult`：验证目标存在、有无接近点、是否可达，返回完整信息
  - `query_assembly_spec(part_id) -> SkillResult`：从 `specs/` 目录读取 YAML，返回工艺约束
  - `request_human_intervention(reason) -> SkillResult`：
    - 设 `RobotContext.instance().halt_flag = True`
    - **需在 Supervisor 节点内通过 LangGraph `interrupt()` 真正暂停图执行**（见 Phase 3.4）

- [ ] **1.3** 新建 `SkiLib/specs/example_assembly.yaml`：示例工艺规范
  - 包含至少 2 个零件 ID（`Part_A`, `Part_B`）、目标位置 ID、工序约束、夹爪类型
  - 命名约定说明：接近点命名规则 `"Approach_<TargetName>"`

---

## Phase 2 · 夹爪原语 + PickAndPlace 重写
*Motion primitives 补全；PickAndPlace 与 RoboDK 树约定对齐*

- [ ] **2.1** 补全 `SkiLib/primitives/motion.py` 中的 `MoveL.check()`
  - 参考 `MoveJ.check()` 实现；调用 `robot.MoveL_Test(current_joints, target_pose)`
  - 返回值：0=成功，-1=无法线性，-2=目标不可达，>0=碰撞数
  - 移除 `@require_robot_active(bypass_halt=False)` 冗余参数，改为无参数形式

- [x] **2.2** 新建 `SkiLib/primitives/gripper.py` *(2026-03-17)*
  - `Grasp(item: robolink.Item, tool: Optional[Item] = None) -> SkillResult`
    - 仿真：`tool.AttachClosest()`；真机：TODO `setDO` + 反馈等待
  - `Release(item: robolink.Item, tool: Optional[Item] = None) -> SkillResult`
    - 仿真：`tool.DetachAll(station)`（物理语义：夹爪开→释放全部）；真机：TODO `setDO`
  - 两个原语均使用 `@require_robot_active` 保护
  - `execution_phase = ExecutionPhase.EXECUTION`（复用现有枚举，不新增 phase）
  - **技术债务**：`robotcontext.py:141,150,167` 三处 `print()` 待 Phase 5.3 统一迁移

- [ ] **2.3** 重写 `SkiLib/skills/pick_and_place.py`
  - **移除 `approach_height` 参数**；接近点从 RoboDK 树中按命名约定取（`GetApproachTarget`）
  - **执行序列**（pick 侧）：`MoveJ(approach_pick)` → `MoveL(pick_target)` → `Grasp` → `MoveL(approach_pick)`
  - **执行序列**（place 侧）：`MoveJ(approach_place)` → `MoveL(place_target)` → `Release` → `MoveL(approach_place)`
  - `REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL', 'Grasp', 'Release', 'GetApproachTarget']`
  - `execute()` 和 `check()` 均返回 `SkillResult`（迁移掉 `CheckResult`）
  - `check()` 验证：pick/place 目标存在、接近点存在、均可达

---

## Phase 3 · Agent 真实实现
*将 graph.py 中的 stub 替换为真实 LLM 调用*

- [ ] **3.1** 新建 `SkiLib/schemas.py`：Pydantic 结构化输出模型
  - `TaskItem(skill: str, params: dict, description: str, expected_result: str)`
  - `TodoList(tasks: List[TaskItem])` — Planner 输出格式
  - 添加 `@field_validator` 检查 `skill` 值是否在已注册技能列表中

- [ ] **3.2** 重写 `graph.py` — **Planner 节点**
  - 使用 `claude-sonnet-4-6` + `with_structured_output(TodoList)`
  - 系统提示：只处理符号/ID，输出合法 `TodoList`，可用技能列表从 `registry.list_skills()` 动态注入
  - Retry 逻辑：Pydantic 校验失败时最多重试 3 次，失败则设 `halt_flag=True`
  - 成功后：用 `RemoveMessage` **清除 Supervisor 阶段产生的全部消息**（防污染 Executor）

- [ ] **3.3** 重写 `graph.py` — **Executor 节点**
  - 从 `registry.get_skill(current_task["skill"])` 动态加载技能实例
  - 调用 `skill.execute(**current_task["params"])`，结果写入 `last_result`
  - 若 skill 不存在：`last_result = {"success": False, "error_type": "UNKNOWN_SKILL", "needs_hilp": True}`
  - 执行完成后：用 `RemoveMessage` 清除本轮 Executor 产生的 ToolMessage 噪音
  - **自愈约定**（重要）：Executor 内部 ReAct 循环负责穷举恢复策略（换参数、换路径等）；
    只有最终放弃时才退出节点并写入 `{"success": False, "needs_hilp": True, ...}`；
    循环中间状态不写 `last_result` 不退出节点。
    `needs_hilp=False + success=False` 的组合禁止从节点输出（语义死角，Context Flush 无法合理处理）。

- [ ] **3.4** 重写 `graph.py` — **Supervisor 节点**
  - 使用 `claude-sonnet-4-6` + LangChain `create_react_agent`
  - 工具集：将 `task_skills.py` 中所有函数包装为 `@tool`（LangChain Tool）
  - LLM schemas 从 `registry.get_llm_tool_schemas(format="anthropic")` 自动注入
  - 终止条件：无 tool_call（知识饱和）或调用了 `request_human_intervention`
  - 调用 `request_human_intervention` 后：**触发 `interrupt("human_intervention")`** 真正暂停图

- [ ] **3.5** 新建/重写 `graph.py` — **`human_intervention` 节点**
  - 在图中增加此节点，接收两类入口：
    - `context_flush` → `halt`（`halt_reason="TASK_FAILURE"`）
    - `dispatcher` → `manual`（`halt_reason="MANUAL_TASK"`）
  - 节点内调用 `interrupt({"halt_reason": ..., "current_task": ..., "todo_list": ...})`
  - Resume 时通过 `Command(resume={"action": "retry"|"complete"|"abort"})` 恢复
  - `action=retry`：清除 `halt_flag/halt_reason`，保留 `current_task`（Executor 重试）
    - ❌ 对 `MANUAL_TASK` 非法，节点内强制降级为 `abort`
  - `action=complete`：清除 `halt_flag/halt_reason`，清空 `current_task`（继续队列）
    - 仅在 `halt_reason="MANUAL_TASK"` 时语义正确
  - `action=abort`：清空 `current_task + todo_list`，清除 `halt_flag/halt_reason`

- [ ] **3.5.1** `graph.py` — **Dispatcher 条件路由**
  - 将 `dispatcher → executor` 静态边改为 `after_dispatcher` 条件函数
  - `type="auto"` → `executor`；`type="manual"` → `human_intervention`；空槽+空队列 → `END`
  - Dispatcher 节点本体：填入 manual 任务时同时设 `halt_flag=True`、`halt_reason="MANUAL_TASK"`

- [ ] **3.5.2** `SkiLib/base.py` — **SkillResult.needs_hilp 字段**
  - 新增 `needs_hilp: bool = True`（已完成，见 base.py）
  - `to_llm_message()` 在 `success=False` 时输出 `needs_hilp`（已完成）
  - Phase 3 Executor ReAct 实现时：只有最终放弃时才退出节点，退出时 `needs_hilp=True`

- [ ] **3.5.3** `graph.py` — **GlobalState 新字段**
  - `halt_reason: Optional[str]`（"TASK_FAILURE" | "MANUAL_TASK" | None）
  - `_hi_action: Optional[str]`（内部路由，human_intervention → after_human_intervention）

- [ ] **3.5.4** `SkiLib/schemas.py` — **TaskItem 支持 manual 任务**
  - `type: Literal["auto", "manual"] = "auto"`
  - `type="manual"` 时 `skill` 可为空，`description` 为必填
  - Planner 系统提示补充：可输出 `type="manual"` 任务

- [ ] **3.6** 重写 `SkiLib/main.py`
  - `RobotContext()` 初始化（一次，进程生命周期内保活）
  - `MemorySaver` checkpointer，固定 `thread_id="main"`
  - `while True` 指令循环：`input()` → `app.invoke(state, config)` → 捕获 `GraphInterrupt` → 提示操作员 resume/abort

---

## Phase 4 · 端到端验证与加固
*系统联调；消除已知风险点*

- [ ] **4.1** 端到端仿真测试（RoboDK 仿真模式，不连接真机）
  - 用例 1：正常指令 "将 Part_A 装入 Tray_1" → Supervisor 查树 → Planner 生成 todo_list → PickAndPlace 执行完整序列
  - 用例 2：目标名称错误 → `GetApproachTarget` 返回 `NO_APPROACH_TARGET` → Executor 失败 → HILP 暂停 → 操作员 abort
  - 用例 3：模糊指令 → Supervisor 调用 `request_human_intervention` → 图暂停 → 操作员补充信息 → resume

- [ ] **4.2** `SkillResult` 迁移扫描
  - 确认无任何代码路径返回 `CheckResult`（除 `base.py` 中保留的兼容桥接）
  - `PickAndPlace.try_execute()` 返回值改为 `SkillResult`

- [ ] **4.3** 消息清理验证
  - 确认 Planner 后 `messages` 中 Supervisor 轮次消息已被 `RemoveMessage` 清除
  - 确认 Context Flush 后 Executor `ToolMessage` 已被清除
  - 测试长序列（10 个任务）时消息列表不膨胀

- [ ] **4.5** 端到端 HILP 路径验证（新增）
  - 用例 4：Planner 输出含 `type="manual"` 任务 → Dispatcher 识别 → `interrupt` 暂停
    → 操作员发 `complete` → Dispatcher 继续剩余自动任务（全程无 halt_flag 残留）
  - 用例 5：Executor 失败（`needs_hilp=True`）→ `halt_reason="TASK_FAILURE"` → 操作员发 `retry`
    → Executor 重试同一任务（`current_task` 未丢失）
  - 用例 6：manual 任务收到非法 `retry` → 节点降级为 `abort`，队列清空，不进入 Executor

- [ ] **4.4** `PrimitiveRegistry` 彻底移除
  - `RobotContext` 不再维护独立的 `PrimitiveRegistry`；统一由 `SkillRegistry` 管理
  - `base.py` 中 `BasePrimitive` 实例化路径全部走 `SkillRegistry`

---

## Phase 5 · 可观测性：print → logging 统一迁移

*将全库所有 `print()` 替换为结构化 logging，同时输出到控制台和日志文件。*

- [x] **5.1** 新建 `SkiLib/log.py`：统一 Logger 工厂 *(2026-03-17，提前实现)*
  - `get_logger(name: str) -> logging.Logger`：返回已配置的模块级 logger
  - 两个 Handler：`StreamHandler`（控制台，同步 `print` 原有行为）+ `RotatingFileHandler`（写入 `logs/roboski.log`，单文件 10 MB，保留 5 份）
  - 格式：`%(asctime)s [%(levelname)s] %(name)s — %(message)s`
  - 日志级别通过环境变量 `ROBOSKI_LOG_LEVEL`（默认 `INFO`）控制，无需改代码切换
  - 首次 import `SkiLib` 时自动完成根 logger 配置（放入 `SkiLib/__init__.py`）

- [ ] **5.2** `SkiLib/graph.py` 迁移
  - 模块顶部：`logger = get_logger("graph")`
  - 各节点 `print()` → `logger.info()` / `logger.debug()` / `logger.warning()`
  - 级别约定：
    - `INFO`：节点入口/出口、任务 dispatch、HILP 触发
    - `DEBUG`：Supervisor 每次 tool_call 详情、Executor 内部重试
    - `WARNING`：保守 fallthrough（`needs_hilp=False + success=False`）、manual 任务 retry 降级
    - `ERROR`：Planner retry 耗尽、未知 skill

- [ ] **5.3** `SkiLib/primitives/` 迁移
  - 各 primitive 模块顶部：`logger = get_logger("primitives.<module_name>")`
  - `execute()` 和 `check()` 中的 `print()` → `logger.debug()`
  - 异常捕获处：`logger.error(..., exc_info=True)` 保留 traceback（不上抛）

- [ ] **5.4** `SkiLib/skills/` 迁移
  - 同 5.3，logger 命名空间：`"skills.<module_name>"`

- [ ] **5.5** `SkiLib/robotcontext.py` 迁移
  - RoboDK 连接成功/失败：`logger.info()` / `logger.error()`
  - Registry 扫描结果：`logger.debug()`

- [ ] **5.6** Notebook (`Agent/LangGraph.ipynb`) 迁移
  - 保留 `logging.basicConfig(level=logging.INFO)` 初始化 cell
  - 节点函数中 `print()` → `logger.info()`（Notebook 环境 StreamHandler 即等同于 print）
  - 测试 cell 中调试用 `print()` 可保留（非生产代码）

- [ ] **5.7** 验证
  - 运行 `SkiLib/main.py`：确认控制台输出与写入 `logs/roboski.log` 内容一致
  - 确认无任何 `print()` 残留于 `SkiLib/` 生产代码（`grep -r "print(" SkiLib/ --include="*.py"`）

---

## 关键文件索引

| 文件 | 状态 | Phase |
|------|------|-------|
| `SkiLib/registry.py` | 待建 | 0.1 |
| `SkiLib/decorators.py` | 待建 | 0.2 |
| `SkiLib/__init__.py` | 待更新 | 0.3 |
| `SkiLib/robotcontext.py` | 待更新 | 0.4 |
| `SkiLib/primitives/scene_query.py` | 待建 | 1.1 |
| `SkiLib/skills/task_skills.py` | 待建 | 1.2 |
| `SkiLib/specs/example_assembly.yaml` | 待建 | 1.3 |
| `SkiLib/primitives/motion.py` | 待更新 | 2.1 |
| `SkiLib/primitives/gripper.py` | 待建 | 2.2 |
| `SkiLib/skills/pick_and_place.py` | 待重写 | 2.3 |
| `SkiLib/schemas.py` | 待建 | 3.1 |
| `SkiLib/graph.py` | 待重写 | 3.2–3.5 |
| `SkiLib/main.py` | 待重写 | 3.6 |

---

## 命名约定（RoboDK 树）

```
RoboDK Tree 中目标点命名规范：
  - 抓取目标：<PartName>_Pick         e.g. "PartA_Pick"
  - 接近点：  Approach_<TargetName>   e.g. "Approach_PartA_Pick"
  - 放置目标：<TrayName>_Place        e.g. "Tray1_Place"
  - 接近点：  Approach_<TargetName>   e.g. "Approach_Tray1_Place"
```

`GetApproachTarget` 按此约定查找；不存在时报 `NO_APPROACH_TARGET` 而非幻觉出坐标。
