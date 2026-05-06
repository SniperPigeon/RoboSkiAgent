# RoboSkiAgent 实现 Checklist

> 按依赖顺序排列。每阶段完成后再进入下一阶段。
> 最后更新：2026-05-06（Genesis 迁移主路径完成；早期 RoboDK 条目保留为历史记录）

---

## 当前状态 · Genesis 迁移收尾

> 下方 Phase 0-6 记录了早期 RoboDK 原型和 Agent 生产化过程。当前主后端已切换为 Genesis；新增工作应优先参考本节、`GENESIS_MIGRATION_PLAN.md` 和 `SkiLib/ARCHITECTURE.md`。

- [x] **G0** 冻结 RoboDK 主路径：不再把 RoboDK 作为当前执行后端 *(2026-05-04)*
- [x] **G1** 新增 Genesis scene/runtime/types：`SkiLib/genesis/scene.py`、`runtime.py`、`types.py` *(2026-04-28)*
- [x] **G2** `RobotContext` 改为 Genesis runtime facade，保留类名减少上层改动 *(2026-04-28)*
- [x] **G3** Genesis metatools 接入 Supervisor：list targets / objects / tools / gripper state *(2026-04-28)*
- [x] **G4** Genesis motion primitives：`MoveJ` / `MoveL`，基于 IK + PD control *(2026-04-28 至 2026-05-04)*
- [x] **G5** Genesis gripper primitives：`Grasp` / `Release`，基于 weld constraint 的 kinematic attachment *(2026-05-04)*
- [x] **G6** `PickAndPlace` 迁移为 Genesis 10 步安全序列 *(2026-05-04)*
- [x] **G7** `GenesisController`：viewer/macOS 下序列化 `scene.step()`，空闲 hold position *(2026-05-04)*
- [x] **G8** `GenesisRuntime.reset()`：支持 episode 重置和后续 rollout worker 设计 *(2026-05-04)*
- [x] **G9** Agent GUI 接入 Genesis，GUI 为当前端到端推荐入口 *(2026-05-04)*
- [x] **G10** README / ROADMAP / CLAUDE / checklist 完成 Genesis 文档纠偏 *(2026-05-06)*
- [ ] **G11** 依赖清理：必要时拆分 `requirements-legacy-robodk.txt`，避免新手误以为 RoboDK 仍是主依赖
- [ ] **G12** CLI interrupt resume：`python -m Agent` 支持 stream + resume，摆脱 GUI 依赖
- [ ] **G13** MoveL 加固：自适应 waypoint、奇异点处理、碰撞/可达性预检增强
- [ ] **G14** 抓取物理增强：从 weld semantic attachment 走向接触/摩擦或真机夹爪反馈

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

## Phase 1 · RoboDK 场景查询层（历史计划，已被 Genesis metatools 取代）
*Supervisor 消歧义的数据来源；Agent 层所有"符号"从这里取。当前主实现见 G3 和 `SkiLib/metatools/informative.py`。*

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

## Phase 2 · 夹爪原语 + PickAndPlace 重写（历史 RoboDK 阶段）
*Motion primitives 补全；PickAndPlace 与 RoboDK 树约定对齐。当前 Genesis 实现见 G4-G6。*

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
  - **执行序列**（8步）：`initial_motion→pick_approach` → `MoveL→pick_target` → `Grasp` → `MoveL→pick_approach` → `transit_motion→place_approach` → `MoveL→place_target` → `Release` → `MoveL→place_approach`
  - **参数** `transit_motion: str = "MoveL"`：携工件过渡段运动类型（默认由 MoveJ 改为 MoveL，2026-03-26）
  - **参数** `initial_motion: str = "MoveL"`：初始进入 pick_approach 的运动类型（原名 `approach_motion`，2026-03-26 重命名并改默认值为 MoveL）
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
  - ⚠️ **设计变更（2026-03-26）**：Planner 改为工具调用方式，不再直接输出 JSON。`TodoList` schema 可降级为内部验证工具（非 LLM 输出格式），`AutoTask`/`ManualTask` 已在 `graph_test.ipynb` 中定义。

- [x] **3.2** `graph_test.ipynb` — **Planner 节点** *(2026-03-26，真实 LLM 实现)*
  - ~~`with_structured_output(TodoList)`~~ ← 已废弃
  - ✅ **实际采用**：`create_agent` + 动态工具调用方式
    - 为每个已注册 Skill 生成 `add_<SkillName>_task` 工具（复用 `try_execute.args_schema`）
    - 新增 `add_manual_task` 工具
    - LLM 按顺序调用工具构建 `plan` 列表，返回后写入 `todo_list`
  - ⚠️ **消息清理未实现**：Supervisor 阶段消息尚未 `RemoveMessage`

- [x] **3.3** `graph_test.ipynb` — **Executor 节点** *(2026-03-26，完整实现)*
  - 直接调用 `skill_registry[skill_name].try_execute(**params)` 执行 `current_task`
  - 失败时进入 LLM 恢复循环（ReAct 内循环）；放弃时抛 `_EscalateHITLException(error_type, reason, suggestion)`
  - 所有路径将 `SkillResult` 对象写入 `last_result`（类型已升为 `Optional[SkillResult]`）
  - 消息清理（`RemoveMessage`）尚未实现，待 Phase 4 补充

- [x] **3.4** `graph_test.ipynb` — **Supervisor 节点** *(2026-03-26，真实 LLM 实现)*
  - ✅ 使用 `create_agent` + `SupervisorOutput` Pydantic schema（structured response）
  - 工具集来自 `SkiLib/metatools/informative.py`（T-skills，只读场景查询）
  - 可用技能列表由代码注入 system prompt（`_get_available_skills()`），LLM 只读取不生成
  - ~~`create_react_agent`~~ 已废弃，改用新 API `create_agent`

- [x] **3.5** `graph_test.ipynb` — **plan_review 节点** *(2026-03-27)*
  - interrupt 结构审批门：`approve` → dispatcher；`replan` → supervisor；`abort` → END
  - 向操作员展示完整 `todo_list` 摘要（task_id / type / skill or description）
  - `replan` 路径：resume payload 为 `{"action": "replan", "feedback": "..."}`，写入 `HumanMessage` + 清空 `todo_list`
  - `plan_review_action` 字段已加入 `GlobalState`
  - ⚠️ **待修**：Planner system prompt 仍残留"在计划前插入 manual task"规则，导致 approve 后多弹一次 complete/abort

- [x] **3.5** `graph_test.ipynb` — **HITL stub 节点** *(2026-03-26，框架重构 + 行为修正)*
  - 原单节点 `human_intervention` 已彻底拆分为：
    - `manual_intervention_handler`：处理 `type="manual"` 任务，actions: `complete` / `abort`
    - `hitl_handler`：处理执行失败，actions: `retry` / `next_task` / `replan` / `abort`
  - 两个节点当前均为 stub（自动选择默认 action），**待实现 `interrupt()` 真正暂停**
  - `hitl_handler` 新增 `replan` 路径（回到 supervisor 重规划），`next_task` 路径（跳过失败任务到 dispatcher）
  - **行为修正（2026-03-26）**：
    - `manual_intervention_handler`：`current_task` 清空改为 `{}`（原 `None`），补齐 `halt_flag/halt_reason` 清零
    - `hitl_handler`：入口打印 `last_result` 诊断；全路径均清 `halt_flag`；`current_task` 应清空路径改为 `{}`

- [x] **3.5.1** `graph_test.ipynb` — **Dispatcher 条件路由** *(2026-03-26)*
  - `task_router` 条件函数：`"auto"` → `executor`；`"manual"` → `manual_intervention_handler`；`"END"` → END
  - `post_task_router` 条件函数（executor 出边）：`"dispatcher"` / `"hitl_handler"` / `"END"`
  - `manual_intervention_router` / `hitl_router` 各自独立

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

- [x] **3.5.5** `graph_test.ipynb` — **端到端 run cell** *(2026-03-26)*
  - 含 `RobotContext` 初始化 + `graph.invoke(initial_state)` + 执行结果摘要打印
  - 验证完整 Supervisor → Planner → Dispatcher → Executor → (HITL stub) 链路可跑通

- [x] **3.5.6** `graph_test.ipynb` — **Planner 工具生成单元测试** *(2026-03-26)*
  - 验证每个已注册 Skill 均生成了对应的 `add_<SkillName>_task` 工具
  - 验证工具的 `args_schema` 与 `try_execute` 签名一致（Pydantic 模型字段对齐）

- [ ] **3.5.7** `graph_test.ipynb` — **日志职责分离（Gradio / HITL interrupt 前置）**
  > **必须在实现 `interrupt()` 真正暂停前完成**：interrupt 挂起后操作员界面需展示干净的执行日志；若 messages 仍混有 LLM 推理链，Gradio 无法安全消费。
  - `messages` 职责收窄：仅用于 LLM Agent 推理链（supervisor 读取上下文），**不再由 executor / planner / hitl_handler 写入状态摘要 AIMessage**
  - `execution_log` 成为唯一展示渠道：补全 supervisor / planner 节点的写入，格式统一为 `"[节点名] 内容"`
  - executor 当前对同一事件双写（`execution_log` + `messages` AIMessage）的冗余写入删除 messages 侧
  - hitl_handler `replan` 路径向 messages 写 `HumanMessage` 触发重规划的行为**保留**（这是 LLM 推理输入，不是展示日志）
  - 验证：`execution_log` 一次完整运行后覆盖 supervisor → planner → dispatcher → executor → hitl/manual 全链路

- [ ] **3.6** 重写 `SkiLib/main.py`
  - `RobotContext()` 初始化（一次，进程生命周期内保活）
  - `MemorySaver` checkpointer，固定 `thread_id="main"`
  - `while True` 指令循环：`input()` → `app.invoke(state, config)` → 捕获 `GraphInterrupt` → 提示操作员 resume/abort

---

## Phase 4 · 端到端验证与加固（历史 RoboDK 阶段）
*系统联调；消除已知风险点。当前 Genesis 端到端路径以 GUI 为准。*

- [ ] **4.1** 端到端仿真测试（RoboDK 仿真模式，不连接真机）
  - 用例 1：正常指令 "将 Part_A 装入 Tray_1" → Supervisor 查树 → Planner 生成 todo_list → PickAndPlace 执行完整序列
  - 用例 2：目标名称错误 → `GetApproachTarget` 返回 `NO_APPROACH_TARGET` → Executor 失败 → HITL 暂停 → 操作员 abort
  - 用例 3：模糊指令 → Supervisor 调用 `request_human_intervention` → 图暂停 → 操作员补充信息 → resume

- [ ] **4.2** `SkillResult` 迁移扫描
  - 确认无任何代码路径返回 `CheckResult`（除 `base.py` 中保留的兼容桥接）
  - `PickAndPlace.try_execute()` 返回值改为 `SkillResult`

- [ ] **4.3** 消息清理验证
  - 确认 Planner 后 `messages` 中 Supervisor 轮次消息已被 `RemoveMessage` 清除
  - 确认 Context Flush 后 Executor `ToolMessage` 已被清除
  - 测试长序列（10 个任务）时消息列表不膨胀

- [ ] **4.5** 端到端 HITL 路径验证（新增）
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
    - `INFO`：节点入口/出口、任务 dispatch、HITL 触发
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

---

## Phase 6 · Notebook → 生产代码迁移
*将 `graph_test.ipynb` 中验证通过的逻辑迁移为可独立运行的 .py 文件；提升可维护性和可测试性*
> 最后更新：2026-03-30（迁移完成）

- [x] **6.1** 抽取 Prompt 到 `Agent/prompts/` 目录 *(2026-03-30)*
  - `supervisor.txt` / `planner.txt` / `executor.txt`（纯文本，运行时 `.format()` 注入）
  - 节点内通过 `_load_prompt(name)` 读取，路径相对于 `nodes/` 解析

- [x] **6.2** 迁移 `GlobalState` 及节点函数到独立 `.py` 文件 *(2026-03-30)*
  - `Agent/state.py`：`GlobalState` TypedDict
  - `Agent/nodes/`：supervisor / planner / plan_review / dispatcher / executor / manual_handler / hitl_handler
  - 路由函数随各节点文件一并迁移（`task_router` / `post_task_router` 等）
  - `Agent/graph.py`：`build_graph()` + `make_initial_state()`，含 `MemorySaver` + `JsonPlusSerializer`
  - 保持 `Agent → SkiLib` 单向依赖

- [x] **6.3** Gradio GUI 迁移到 `Agent/gui.py` *(2026-03-30)*
  - `start_flow` / `handle_choice` / `_check_for_interrupt` 完整迁移
  - `python -m Agent.gui` 直接启动

- [x] **6.4** CLI 入口 `Agent/__main__.py` *(2026-03-30)*
  - `python -m Agent "<指令>"` 可调用图执行
  - 支持 `--skip-check` bypass 模式

- [ ] **6.5** CLI interrupt handling ⚠️ **尚未实现**
  - 当前 `__main__.py` 使用 `graph.invoke()`，遇到任意 `interrupt()` 节点（`plan_review` / `hitl_handler` / `manual_intervention_handler`）会抛出 `NodeInterrupt` 异常，导致 CLI 无法完成含人工审批的完整流程
  - **解决方案**：改用 `graph.stream()` + 捕获 `GraphInterrupt`，提示操作员输入后调用 `graph.invoke(Command(resume=...))` 恢复
  - **现状**：GUI（`Agent/gui.py`）已实现完整 interrupt 处理，推荐使用 GUI

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
| `SkiLib/robotcontext.py` | ✅ Genesis runtime facade | G2 |
| `SkiLib/base.py` | ✅ 完成（SkillResult, TOOL_METHODS, as_tools(), require_robot_active） | — |
| `SkiLib/genesis/scene.py` | ✅ Genesis scene builder（UR16e + Robotiq + objects + targets） | G1 |
| `SkiLib/genesis/runtime.py` | ✅ GenesisRuntime（registries + reset + gripper state） | G1 / G8 |
| `SkiLib/genesis/controller.py` | ✅ viewer/macOS thread serializer | G7 |
| `SkiLib/genesis/motion.py` | ✅ IK / control helpers | G4 |
| `SkiLib/genesis/types.py` | ✅ SceneTarget / SceneObject / TargetPose | G1 |
| `SkiLib/primitives/motion.py` | ✅ Genesis MoveJ + MoveL；待增强碰撞/奇异点检查 | G4 / G13 |
| `SkiLib/primitives/gripper.py` | ✅ Genesis Grasp + Release；weld constraint 语义抓取 | G5 / G14 |
| `SkiLib/skills/pick_and_place.py` | ✅ Genesis 10步序列，显式接近点参数 | G6 |
| `SkiLib/utils.py` | ⚠️ 有 print()，待迁移 | 5.5.1 |
| `SkiLib/primitives/scene_query.py` | 历史 RoboDK 计划；当前不建 | 1.1 |
| `SkiLib/skills/task_skills.py` | 历史 RoboDK 计划；Genesis metatools 已覆盖基础查询 | 1.2 / G3 |
| `SkiLib/specs/example_assembly.yaml` | 后续工艺知识扩展项 | 1.3 |
| `SkiLib/schemas.py` | ❌ 待建 | 3.1 |
| `SkiLib/metatools/informative.py` | ✅ 完成（T-skills: list_targets/objects/tools, check_item_exists, get_gripper_state） | 新增 |
| `Agent/state.py` | ✅ 完成（GlobalState TypedDict） | 6.2 |
| `Agent/graph.py` | ✅ 完成（build_graph + make_initial_state） | 6.2 |
| `Agent/llm.py` | ✅ 完成（claude / ollama 工厂） | 6.2 |
| `Agent/gui.py` | ✅ 完成（Gradio，完整 interrupt 支持） | 6.3 |
| `Agent/__main__.py` | ⚠️ 完成，CLI interrupt handling 未实现（见 6.5） | 6.4 |
| `Agent/prompts/` | ✅ 完成（supervisor.txt / planner.txt / executor.txt） | 6.1 |
| `Agent/nodes/` | ✅ 完成（7 个节点均已迁移） | 6.2 |
| `Agent/notebooks/graph_test.ipynb` | 参考文档（核心逻辑已迁移至 Agent/ 包） | — |
| `SkiLib/graph.py` | ⚠️ 错位文件（含 LangGraph 依赖，违反 SkiLib 约束）；待废弃 | — |
| `SkiLib/main.py` | ⚠️ 调试用 | 3.6 |

---

## 命名约定（Genesis 当前场景）

```
对象：
  - Part_A_1
  - Part_B_1
  - Part_C_1

目标：
  - Home_position
  - PartA_Approach / PartA_Pick
  - PartB_Approach / PartB_Pick
  - PartC_Approach / PartC_Pick
  - AssemblySlot_1_Approach / AssemblySlot_1
  - AssemblySlot_2_Approach / AssemblySlot_2
  - AssemblySlot_3_Approach / AssemblySlot_3
```

LLM-facing 参数必须使用这些符号名。接近点当前是显式参数，不通过 `GetApproachTarget` 自动查找。
