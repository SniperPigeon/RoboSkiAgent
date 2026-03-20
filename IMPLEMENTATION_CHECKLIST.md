# RoboSkiAgent 实现 Checklist

> 按依赖顺序排列。每阶段完成后再进入下一阶段。
> 最后更新：2026-03-20（graph.py 路由重构）

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

## Phase 0.5 · 设计决策记录（已落地，无对应 Phase）

- [x] **D1** `BaseSkill.TOOL_METHODS = ("check", "try_execute")` *(2026-03-20)*
  - `execute` 从 LLM 工具列表中移除：跳过校验的调用路径不应暴露给 LLM
  - `check` 保留：LLM 可做"看而不动"的可行性探测
  - `try_execute` 是 LLM 的正常执行路径（内置安全校验）
  - 子类可通过覆盖 `TOOL_METHODS` 调整（如需暴露 `execute` 的特殊场景）

- [x] **D2** `Grasp`/`Release` 参数 `item` → `expected_item` *(2026-03-20)*
  - 夹爪无感知能力：`AttachClosest()` 基于距离，`DetachAll()` 释放全部
  - `expected_item` 仅用于预检有效性和溯源日志，不影响实际夹取对象
  - `Grasp.execute()` 新增 `intended/attached` 差异日志，便于调试站点配置错误

- [x] **D3** `PickAndPlace` 接近点改为显式参数 *(2026-03-20)*
  - 原规划（2.3）：接近点由 `GetApproachTarget` 按命名约定 `"Approach_<target>"` 自动查找
  - 实际实现：`pick_approach` / `place_approach` 作为显式字符串参数传入
  - **理由**：LLM 应明确指定所有目标符号；隐式约定对 LLM 不透明且增加不必要的 primitive 依赖
  - 命名约定（`Approach_*`）保留为 RoboDK 站点规范建议，但不在代码层强制

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

- [x] **2.1** 补全 `SkiLib/primitives/motion.py` 中的 `MoveL.check()` *(2026-03-13)*
  - 参考 `MoveJ.check()` 实现；调用 `robot.MoveL_Test(current_joints, target_pose)`
  - 返回值：0=成功，-1=无法线性，-2=目标不可达，>0=碰撞数
  - 移除 `@require_robot_active(bypass_halt=False)` 冗余参数，改为无参数形式

- [x] **2.2** 新建 `SkiLib/primitives/gripper.py` *(2026-03-17)*
  - `Grasp(expected_item: robolink.Item, tool: Optional[Item] = None) -> SkillResult`
    - 仿真：`tool.AttachClosest()`；真机：TODO `setDO` + 反馈等待
    - **参数命名**：`item` → `expected_item`（2026-03-20）：夹爪无法选择目标，`expected_item` 仅用于预检和溯源日志，不决定抓哪个
    - `execute()` 新增溯源日志：`intended='X', attached='Y'`，暴露预期与实际附着的差异
  - `Release(expected_item: robolink.Item, tool: Optional[Item] = None) -> SkillResult`
    - 仿真：`tool.DetachAll(station)`（物理语义：夹爪开→释放全部）；真机：TODO `setDO`
  - 两个原语均使用 `@require_robot_active` 保护
  - `execution_phase = ExecutionPhase.EXECUTION`（复用现有枚举，不新增 phase）
  - **技术债务**：`robotcontext.py` 三处 `print()` 待 Phase 5.5 统一迁移

- [x] **2.3** 重写 `SkiLib/skills/pick_and_place.py` *(2026-03-20)*
  - **实现方案与原规划不同**：接近点改为显式参数（`pick_approach`、`place_approach`），不采用命名约定自动查找（`GetApproachTarget`）
    - **理由**：LLM 作为调用方应明确指定所有目标，隐式命名约定对 LLM 不透明，且增加对 Phase 1 `scene_query` 的不必要依赖
    - `REQUIRED_PRIMITIVES = ['MoveJ', 'MoveL', 'Grasp', 'Release']`（无需 `GetApproachTarget`）
  - **执行序列**（8步）：`approach_motion→pick_approach` → `MoveL→pick_target` → `Grasp` → `MoveL→pick_approach` → `transit_motion→place_approach` → `MoveL→place_target` → `Release` → `MoveL→place_approach`
  - **新增参数** `transit_motion: str = "MoveJ"`：控制携工件过渡段（`pick_approach→place_approach`）的运动类型
  - **新增参数** `approach_motion: str = "MoveJ"`：控制初始进入接近点（`→pick_approach`）的运动类型
  - `execute()` / `check()` / `try_execute()` 均返回 `SkillResult`，无 `CheckResult` 使用
  - `check()` 验证：所有目标存在性（symbol resolve）+ 各段运动可达性（调用 primitive.check()）
  - `try_execute()` 支持 `_should_skip_check()` bypass（调试用）

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

- [x] **3.5** `graph.py` — **`human_intervention` stub 节点** *(2026-03-20，stub)*
  - ~~新建~~ 已加入图；接收两类入口：
    - `context_flush` → `halt`（`halt_reason="TASK_FAILURE"`）
    - `after_dispatcher` → `manual`（`halt_reason="MANUAL_TASK"`）
  - **stub 自动行为**：`MANUAL_TASK` → `complete`；`TASK_FAILURE` → `abort`
  - `retry` 对 `MANUAL_TASK` 非法，节点内强制降级为 `abort`（已实现）
  - **待实现（Phase 3.5 真实版）**：节点内调用 `interrupt({...})`，通过 `Command(resume={"action": ...})` 恢复

- [x] **3.5.1** `graph.py` — **Dispatcher 条件路由** *(2026-03-20)*
  - `dispatcher → executor` 静态边已改为 `after_dispatcher` 条件函数
  - `halt_flag=False` → `executor`（auto 任务）；`halt_flag=True` → `human_intervention`（manual 任务）
  - Dispatcher 填入 manual 任务时同时设 `halt_flag=True`、`halt_reason="MANUAL_TASK"`
  - `should_continue` 的 `halt` 路径已从 `END` 改路由到 `human_intervention`
  - `context_flush` 失败路径补充 `halt_reason="TASK_FAILURE"`

- [ ] **3.5.2** `SkiLib/base.py` — **SkillResult.needs_hilp 字段**
  - 新增 `needs_hilp: bool = True`（已完成，见 base.py）
  - `to_llm_message()` 在 `success=False` 时输出 `needs_hilp`（已完成）
  - Phase 3 Executor ReAct 实现时：只有最终放弃时才退出节点，退出时 `needs_hilp=True`

- [x] **3.5.3** `graph.py` — **GlobalState 新字段** *(2026-03-20)*
  - `halt_reason: Optional[str]`（"TASK_FAILURE" | "MANUAL_TASK" | None）✅
  - `_hi_action: Optional[str]`（内部路由，human_intervention → after_human_intervention）✅

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

- [ ] **5.2** `SkiLib/graph.py` 迁移（stub 阶段大量 `print()`，待 Phase 3 重写时一并完成）
  - 模块顶部：`logger = get_logger("graph")`
  - 各节点 `print()` → `logger.info()` / `logger.debug()` / `logger.warning()`
  - 级别约定：
    - `INFO`：节点入口/出口、任务 dispatch、HILP 触发
    - `DEBUG`：Supervisor 每次 tool_call 详情、Executor 内部重试
    - `WARNING`：保守 fallthrough（`needs_hilp=False + success=False`）、manual 任务 retry 降级
    - `ERROR`：Planner retry 耗尽、未知 skill

- [x] **5.3** `SkiLib/primitives/` 迁移 *(2026-03-20)*
  - `gripper.py`：已完成，`get_logger(__name__)` + 全部 `logger.*` 调用
  - `motion.py`：无 `print()` 残留，暂无 logger 调用（纯计算分支，后续按需补充）

- [x] **5.4** `SkiLib/skills/` 迁移 *(2026-03-20)*
  - `pick_and_place.py`：已完成，`get_logger(__name__)` + 8 步执行日志均用 `logger.info()`
  - `dummy_skills.py`：测试桩文件，保留 `print()`（非生产代码）

- [ ] **5.5** `SkiLib/robotcontext.py` 迁移
  - 三处 `print()` 待迁移（`PrimitiveRegistry` 扫描/注册日志）
  - RoboDK 连接成功/失败：`logger.info()` / `logger.error()`

- [ ] **5.5.1** `SkiLib/utils.py` 迁移
  - 三处 `print()` 在异常捕获块内（IK solving、singularity check、manipulability）
  - 改为 `logger.error(..., exc_info=True)`

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
| `SkiLib/log.py` | ✅ 完成 | 5.1 |
| `SkiLib/registry.py` | ✅ 完成 | 0.1 |
| `SkiLib/decorators.py` | ✅ 不建（见 0.2 决策） | 0.2 |
| `SkiLib/__init__.py` | ✅ 完成 | 0.3 |
| `SkiLib/robotcontext.py` | ⚠️ 完成，3处 print() 待迁移 | 0.4 / 5.5 |
| `SkiLib/base.py` | ✅ 完成（SkillResult, TOOL_METHODS, as_tools(), require_robot_active） | — |
| `SkiLib/primitives/motion.py` | ✅ 完成（MoveJ + MoveL，含 check()） | 2.1 |
| `SkiLib/primitives/gripper.py` | ✅ 完成（Grasp + Release，expected_item） | 2.2 |
| `SkiLib/skills/pick_and_place.py` | ✅ 完成（8步序列，显式接近点参数） | 2.3 |
| `SkiLib/utils.py` | ⚠️ 有 print()，待迁移 | 5.5.1 |
| `SkiLib/primitives/scene_query.py` | ❌ 待建 | 1.1 |
| `SkiLib/skills/task_skills.py` | ❌ 待建 | 1.2 |
| `SkiLib/specs/example_assembly.yaml` | ❌ 待建 | 1.3 |
| `SkiLib/schemas.py` | ❌ 待建 | 3.1 |
| `SkiLib/graph.py` | ⚠️ stub；路由重构完成（3.5.1/3.5.3 ✅）；LLM 节点待重写（3.2–3.4/3.5 真实版） | 3.2–3.5 |
| `SkiLib/main.py` | ⚠️ 调试用，待重写 | 3.6 |

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
