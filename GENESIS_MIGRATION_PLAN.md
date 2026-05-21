# Genesis 迁移计划

> 目标：将项目从 RoboDK 执行后端整体迁移到 Genesis。Genesis 是长期目标，因此本计划允许不兼容切换，不再维持 RoboDK 双后端兼容。

## 当前判断

项目目前的分层大致是：

- `Agent/`：LangGraph 编排层，通过 `SkillRegistry` 调用技能。
- `SkiLib/skills/`：理论上平台无关，`PickAndPlace` 主要负责编排 primitive。
- `SkiLib/primitives/`：当前强绑定 RoboDK，包含 `MoveJ`、`MoveL`、`Grasp`、`Release`。
- `SkiLib/robotcontext.py`：RoboDK 连接单例，同时负责 primitive registry 初始化。
- `SkiLib/metatools/informative.py`：Supervisor 使用的只读场景查询工具，但目前直接查询 RoboDK station。
- `SkiLib/sensors/gripper.py`：执行阶段夹爪状态查询，目前依赖 RoboDK tool-child 关系。
- `res/genesis_scene_test.py`：Genesis 最小场景，已包含 UR16e + Robotiq、桌面、托盘、零件和基础命名目标。

关键结论：RoboDK 依赖不只在 primitive 层。符号解析、场景查询、夹爪状态、RobotContext 初始化都绑定了 RoboDK。因此迁移应以“替换运行时上下文”为主线，而不是只重写 `primitives/`。

## 迁移原则

本次迁移采用不兼容切换：

- 删除或废弃 RoboDK 运行路径。
- `RobotContext` 直接变成 Genesis 上下文。
- 上层 Agent 和 Skill 尽量保持调用方式稳定，但不承诺兼容 RoboDK 的 Item / Station / Target 概念。
- 对 LLM 暴露的接口仍然使用符号名，不暴露坐标、矩阵或 Genesis entity。
- `PickAndPlace` 保持“组合 primitive”的职责，但内部符号解析改为 Genesis scene registry。

目标依赖方向：

```text
Agent
  -> SkillRegistry
    -> PickAndPlace / other skills
      -> MoveJ / MoveL / Grasp / Release
        -> GenesisRuntime / GenesisScene
          -> Genesis simulator
```

## 目标结构

建议最终结构：

```text
SkiLib/
  base.py                       # 平台无关：SkillResult / RobotState / BasePrimitive / BaseSkill
  robotcontext.py               # Genesis runtime singleton，保留旧文件名以减少上层改动
  genesis/
    __init__.py
    scene.py                    # 从 res/genesis_scene_test.py 提炼出的 scene builder
    runtime.py                  # GenesisRuntime：scene/entity/target/object/gripper 状态
    types.py                    # TargetPose / SceneTarget / SceneObject 等数据结构
    motion.py                   # IK、轨迹采样、PD 控制辅助函数
    gripper.py                  # 夹爪开闭和 kinematic attachment 辅助函数
  primitives/
    motion.py                   # Genesis 版 MoveJ / MoveL
    gripper.py                  # Genesis 版 Grasp / Release
  metatools/
    informative.py              # 调用 RobotContext 的 Genesis registry
  sensors/
    gripper.py                  # 调用 GenesisRuntime 的 held-item 状态
```

`res/genesis_scene_test.py` 保留为实验脚本，但核心搭建逻辑迁到 `SkiLib/genesis/scene.py`。

## 核心数据模型

Genesis 不应模拟 RoboDK `Item`。改用项目自己的轻量句柄。

```python
@dataclass(frozen=True)
class TargetPose:
    name: str
    pos: tuple[float, float, float]
    quat: tuple[float, float, float, float]
    kind: Literal["home", "approach", "pick", "place"]


@dataclass(frozen=True)
class SceneTarget:
    name: str
    pose: TargetPose


@dataclass
class SceneObject:
    name: str
    entity: object
```

上层只传字符串。`RobotContext.resolve_target(name)` / `resolve_object(name)` 返回这些句柄，只有 primitive 使用它们。

## Genesis Scene 迁移

将 `res/genesis_scene_test.py` 拆成可复用函数：

```python
def build_genesis_scene(show_viewer: bool = False) -> GenesisSceneBundle:
    ...
```

`GenesisSceneBundle` 至少包含：

- `scene`
- `robot`
- `objects: dict[str, SceneObject]`
- `targets: dict[str, SceneTarget]`
- `tools: dict[str, object]`
- `arm_dofs`
- `gripper_dofs`
- `home_qpos`
- `tcp_link` 或 TCP link 名称

现有 `NAMED_TARGETS` 只有 xyz，不够执行 IK。需要升级为完整位姿。第一阶段可以统一使用固定的 top-down 夹爪姿态。

必须补齐的目标名：

- `Home_position`
- `PartA_Approach`
- `PartB_Approach`
- `PartC_Approach`
- `PartA_Pick`
- `PartB_Pick`
- `PartC_Pick`
- `AssemblySlot_1_Approach`
- `AssemblySlot_2_Approach`
- `AssemblySlot_3_Approach`
- `AssemblySlot_1`
- `AssemblySlot_2`
- `AssemblySlot_3`

对象名建议与当前资源保持一致：

- `Part_A_1`
- `Part_B_1`
- `Part_C_1`

## Primitive 实现方案

### MoveJ

语义：关节空间点到点运动。

实现步骤：

1. 接收 `SceneTarget` 或关节列表。
2. 如果是 target，则调用 Genesis IK 得到目标关节。
3. `check()` 验证目标存在、IK 可解、关节数量和关节限位。
4. `execute()` 使用 `robot.control_dofs_position()` 控制 arm dofs。
5. 每步 `scene.step()`，直到关节误差小于阈值或超时。
6. 成功返回 `SkillResult(success=True, execution_phase=EXECUTION)`。

第一阶段可以不实现完整碰撞检测，但要明确返回信息：当前只检查 IK / joint limit，不等价于 RoboDK `MoveJ_Test`。

### MoveL

语义：TCP 笛卡尔直线运动。

实现步骤：

1. 接收 `SceneTarget`。
2. 读取当前 TCP 位姿。
3. 在当前 TCP 位姿和目标 TCP 位姿之间采样直线路径。
4. 每个 waypoint 调用 IK，以上一个 waypoint 的解作为 seed。
5. `check()` 任一 waypoint 无解则返回 `ERROR_IK_FAILURE`。
6. `execute()` 逐 waypoint 控制关节，并在每段内 step scene。

第一阶段建议固定采样数，例如 20 个 waypoint。后续再增加自适应步长、奇异点检测和碰撞检查。

### Grasp

RoboDK 的 `AttachClosest()` 在 Genesis 中没有直接等价物。第一阶段采用 kinematic attachment。

实现步骤：

1. 检查 `expected_item` 是否存在。
2. 计算 TCP 与目标物体中心的距离。
3. 距离低于阈值时关闭夹爪，并设置 `runtime.held_item_name = expected_item.name`。
4. 之后每次 scene step 前后，将 held object 的位姿同步到 TCP 下方的固定 offset。
5. `get_gripper_state()` 根据 `held_item_name` 返回状态。

这不是物理抓取，只是仿真执行语义。它足够支持 Agent / Skill 迁移和任务验证。

### Release

实现步骤：

1. 打开夹爪。
2. 将 held object 留在当前世界位姿。
3. 清空 `runtime.held_item_name`。
4. 可选 step 若干帧让物体稳定。

## RobotContext 改造

直接把 `SkiLib/robotcontext.py` 改为 Genesis runtime facade。保留类名 `RobotContext`，减少 Agent 和 Skill 的修改面。

新职责：

- 初始化 Genesis scene。
- 持有 `GenesisRuntime`。
- 初始化 `PrimitiveRegistry` 和 `SkillRegistry`。
- 提供 `list_targets()`、`list_objects()`、`list_tools()`、`check_item_exists()`。
- 提供 `resolve_target()`、`resolve_object()`。
- 提供 `get_current_state()` 和 `get_gripper_state()`。

废弃职责：

- 不再连接 RoboDK。
- 不再暴露 `RDK`。
- 不再暴露 RoboDK `Item`。
- 不再依赖 `RunMode()` 判断仿真/真机。

短期兼容处理：

- 所有使用 `ctx.RDK.Item(name)` 的地方必须改掉。
- `PickAndPlace._resolve()` 改为调用 `ctx.resolve_item()` 或区分 `resolve_target()` / `resolve_object()`。
- `metatools` 改为调用 `ctx.list_targets()` 等方法。
- `sensors` 改为调用 `ctx.get_gripper_state()`。

## 依赖迁移

`robodk` 不再是主依赖。建议：

1. 从 `requirements.txt` 移除 `robodk`。
2. 保留 `genesis-world`。
3. 如果还需要打开旧代码做参考，新建 `requirements-legacy-robodk.txt`。
4. `environment.yml` 中移除 `robodk`，确认 Genesis 所需的 `torch` / `numpy` / 图形依赖。

建议依赖文件：

```text
requirements.txt                 # Agent + SkiLib + Genesis runtime
requirements-agl.txt             # 训练相关，保持独立
requirements-legacy-robodk.txt   # 仅旧 RoboDK 参考，不参与主流程
```

同时清理 import：

- `SkiLib/base.py` 删除 `from robodk import ...`。
- `SkiLib/robotcontext.py` 删除所有 RoboDK import。
- `SkiLib/primitives/motion.py` 删除 RoboDK 类型判断。
- `SkiLib/primitives/gripper.py` 删除 RoboDK tool API。
- `SkiLib/metatools/informative.py` 删除 `robolink`。
- `SkiLib/sensors/gripper.py` 删除运行时 RoboDK 查询。

## 实施阶段

### Phase 0：冻结 RoboDK 旧路径

- 标记当前 RoboDK 文件为待替换。
- 不再新增 RoboDK 功能。
- 确认 `res/genesis_scene_test.py` 能启动并 hold home。
- 记录 robot joints、dofs、TCP link、gripper dofs。

验收：

- Genesis scene 可运行。
- 明确 UR16e arm dofs 和 Robotiq gripper dofs。

### Phase 1：抽出 Genesis Scene Runtime

- [x] 新建 `SkiLib/genesis/types.py`。
- [x] 新建 `SkiLib/genesis/scene.py`。
- [x] 新建 `SkiLib/genesis/runtime.py`。
- [x] 将 `res/genesis_scene_test.py` 的场景搭建迁移进去。
- [x] 添加 `Home_position` 和 approach targets。
- [x] 处理当前 macOS/conda headless 环境问题：Genesis CPU 名称 fallback、`XDG_CACHE_HOME`/`MPLCONFIGDIR` 重定向、headless 默认跳过 visualizer build。

验收：

- [x] 可以通过 `RobotContext()` 初始化 Genesis。
- [x] 可以列出 targets、objects、tools。
- [x] 可以获取当前 `RobotState`。

### Phase 2：替换 RobotContext 和 metatools

- [x] 改造 `SkiLib/robotcontext.py` 为 Genesis context。
- [x] 改造 `SkiLib/metatools/informative.py`。
- [x] 改造 `SkiLib/sensors/gripper.py`。
- [x] 删除共享层 RoboDK import。
- [x] 将 `MoveJ` / `MoveL` / `Grasp` / `Release` 临时替换为 Genesis 占位 primitive，保证 registry 可启动。

验收：

- [x] `list_targets()` 返回 Genesis targets。
- [x] `list_objects()` 返回 Genesis parts。
- [x] `check_item_exists("Part_A_1")` 返回 true。
- [x] 导入 `SkiLib.base` 不需要 `robodk`。

### Phase 3：实现 Genesis Motion Primitives

- [x] 重写 `SkiLib/primitives/motion.py`。
- [x] 新建 `SkiLib/genesis/motion.py`，集中封装 IK、TCP 查询、关节控制和 waypoint 插值。
- [x] 实现 `MoveJ.check/execute/try_execute`。
- [x] 实现 `MoveL.check/execute/try_execute`。
- [x] 为 IK failure、timeout、invalid target 映射现有 `SkillResult` 错误类型。
- [x] 添加 `tests/genesis_motion_smoke.py`，验证 Home → PartA_Approach → PartA_Pick。

验收：

- [x] `MoveJ` 到 `Home_position`。
- [x] `MoveL` 到 `PartA_Approach`。
- [x] `MoveL` 从 approach 到 pick target。
- [x] 失败时不抛原始异常，统一返回 `SkillResult`。

### Phase 4：实现 Genesis Gripper Primitives

> ✅ **完成（2026-05-05 核实）**

- [x] 重写 `SkiLib/primitives/gripper.py`。
- [x] 实现夹爪 open/close qpos（`runtime.open_gripper()` + `set_dofs_position`）。
- [x] 实现 kinematic attachment（Genesis `rigid_solver.add_weld_constraint`）。
- [x] `get_gripper_state()` 和 sensor tools 读取 held item。

验收：

- [x] 到达 `PartA_Pick` 后 `Grasp(“Part_A_1”)` 成功。
- [x] 移动过程中 `Part_A_1` 跟随 TCP。
- [x] `Release(“Part_A_1”)` 后 held state 清空。

**实际实现与原计划的偏差：**
- `Grasp` 判定距离阈值为 0.18 m（捕获半径较大，对应 UR16e 腕部到夹爪指尖的完整偏移量）。
- weld constraint 绑定的是物体质心 link 与 TCP link（`wrist_3_link`），不是夹爪指尖。
- `open_gripper()` 同时调用 `set_dofs_position` + `control_dofs_position`，保证 viewer 立即反映开爪状态。

**未计划但已实现：**
- `GenesisRuntime.reset()`：解除 weld constraint，清空 `held_item_name`，调用 `scene.reset()`，恢复 home_qpos，重新开爪。供每轮测试/训练的 episode 重置使用。

---

### Phase 4.5：GenesisController（原计划未包含，实际新增）

> ✅ **完成（2026-05-05 核实）**，文件：`SkiLib/genesis/controller.py`

Genesis 要求所有 `scene.step()` 调用在同一线程（macOS pyrender 限制）。Agent/Skill 运行于 LangGraph worker 线程，与 viewer 主线程存在冲突。为此引入 `GenesisController`：

- 单线程任务队列（`queue.Queue`），将 primitive 执行函数序列化到 Genesis 主线程。
- 空闲时以 ~60 fps 主动 hold 手臂和夹爪位置，防止重力下沉。
- worker 线程调用 `ctrl.submit(fn, ...)` → 阻塞等待 `Future.result()`，对 primitive 代码透明。
- `is_genesis_thread()` 检测防止主线程直接调用时死锁。

---

### Phase 5：迁移 PickAndPlace

> ✅ **完成（2026-05-05 核实）**

- [x] 修改 `SkiLib/skills/pick_and_place.py` 的符号解析。
- [x] 检查 `check()` 中每一步的 primitive 调用是否仍合理。
- [x] 保持 LLM-facing 参数仍然是字符串。

验收：

- [x] `PickAndPlace.try_execute(...)` 可以完成单个零件从 parts tray 到 assembly tray。
- [x] pre-flight failure 能返回清晰错误。

**实际实现与原计划的偏差：**
- 原计划 8 步执行序列，实际实现为 **10 步**（增加了 pick_approach 归位和 place_approach 归位两步，对应 “抬升后才平移” 的安全策略）：
  1. `initial_motion` → home_position
  2. MoveL → pick_approach
  3. MoveL → pick_target
  4. Grasp item
  5. MoveL → pick_approach（归位抬升）
  6. `transit_motion` → place_approach
  7. MoveL → place_target
  8. Release item
  9. MoveL → place_approach（归位抬升）
  10. MoveL → home_position
- 参数：`item, home_position, pick_approach, pick_target, place_approach, place_target, transit_motion=”MoveL”, initial_motion=”MoveL”`
- `check()` 对每个步骤都做符号存在性 + IK 可达性 pre-flight 验证。

---

### Phase 6：Agent 集成

> ✅ **完成（2026-05-05 核实）**

- [x] 用 Genesis metatools 驱动 Supervisor。
- [x] Planner 继续生成 `PickAndPlace` 任务。
- [x] Executor 通过 `SkillRegistry` 调用 Genesis 版技能。
- [x] GUI / CLI 初始化 Genesis context。

验收：

- [x] Agent 能看到 Genesis targets 和 objects。
- [x] 计划审批后能执行一个零件 pick-and-place。
- [x] 失败进入现有 HITL 路径。

**遗留问题（Phase 6 完成但未修复）：**
- CLI 入口（`python -m Agent “<指令>”`）使用 `graph.invoke()`，遇到 interrupt 节点会抛 `NodeInterrupt` 异常；含 HITL 审批的完整流程须通过 GUI 运行。

---

### Phase 7：清理文档和依赖

> 🔶 **进行中（2026-05-06 文档主路径已纠偏，依赖清理未完成）**

- [x] 更新 `README.md`。
- [x] 更新 `ROADMAP.md`。
- [x] 更新 `CLAUDE.md` 技术栈和目录结构。
- [x] 更新 `SkiLib/ARCHITECTURE.md`。
- [x] 更新 `IMPLEMENTATION_CHECKLIST.md`。
- [x] 移除或归档主入口文档中的 RoboDK 主后端叙事。
- [ ] 清理 `requirements.txt` / `environment.yml`。

验收：

- [x] 主入口文档不再把 RoboDK 描述为主仿真后端。
- [ ] 新人按 README 能启动 Genesis 场景和 Agent。（待实际环境验证）

---

## 风险与待确认问题

> 以下为原计划记录，已在实现中确认的条目保留说明。

1. ~~Genesis IK API 的具体调用方式和返回失败的表现。~~ → **已解决**：使用 `robot.inverse_kinematics(link, pos, quat, init_qpos, dofs_idx_local, return_error=True)`；位置误差阈值 <0.005 m，旋转 <0.05 rad。
2. ~~URDF 中 TCP link 名称，需要确认 Robotiq 指尖或 tool0 作为控制 TCP。~~ → **已解决**：控制 TCP 为 `wrist_3_link`，在 scene.py 中加固定偏移 Z = 0.172 m（到 Robotiq 2F-85 指尖接触面）。
3. ~~Robotiq mimic joints 是否能简单用 dof position 控制。~~ → **已解决**：仅控制第一个夹爪 dof（`gripper_dofs`），mimic joint 由 URDF 内部约束处理。
4. ~~MoveL 的姿态插值是否需要四元数 slerp，第一阶段可固定姿态。~~ → **已按计划降级**：MoveL 固定 top-down 姿态，不做 slerp；足够支持当前装配任务。
5. ~~碰撞检测和 RoboDK `MoveJ_Test` / `MoveL_Test` 不等价，第一阶段应明确降级。~~ → **已明确降级**：当前无碰撞检测，仅依赖 IK 失败和超时判定失败；后续可扩展。
6. kinematic attachment 与物理接触不同，后续如要真实抓取，需要单独做接触/摩擦建模。→ **仍开放**，当前 weld constraint 足以验证计划正确性。

**新增风险（实现过程中发现）：**

7. **传送带追踪不可行**：当前 `SceneTarget` 为 frozen dataclass，`control_to_qpos` 驱动到固定 qpos，无法跟踪运动中的物体。若要支持传送带动态追踪，需新增追踪控制循环（每步查 entity live pos → IK → 控制）。
8. **MoveL 采样固定**：20 个 waypoint，无自适应步长。长距离直线或关节空间跨度大时可能因中间 IK 失败而整段失败，也可能在接近奇异点时跳变。
9. **episode reset 与 weld constraint 耦合**：`reset()` 在删除 weld constraint 时若仿真已出现约束损坏，可能静默失败；多 episode 长期运行需关注。
10. **并发 rollout 不支持**：`GenesisRuntime` 是进程级单例，RL 多进程 rollout 须在不同进程中分别初始化，不能共享 controller。

---

## ~~建议第一个开发切片~~

> ⚠️ **此节已过期**（Phase 1–6 已完成，保留为历史记录）

~~第一步不要直接写 MoveL。先建立 Genesis runtime 骨架：~~

~~1. 新建 `SkiLib/genesis/types.py`。~~
~~2. 新建 `SkiLib/genesis/scene.py`，把 `res/genesis_scene_test.py` 拆进去。~~
~~3. 新建 `SkiLib/genesis/runtime.py`，实现 targets / objects / tools registry。~~
~~4. 改造 `RobotContext` 初始化 Genesis。~~
~~5. 改造 `metatools/informative.py`，让 Supervisor 能列出 Genesis 世界里的符号。~~

~~完成这一步后，上层 Agent 就能”看见” Genesis 场景；随后再逐个迁移 motion 和 gripper primitive。~~

---

## 当前状态总览（2026-05-05 核实）

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 0 | 冻结 RoboDK 旧路径 | ✅ |
| Phase 1 | Genesis Scene Runtime | ✅ |
| Phase 2 | RobotContext + metatools | ✅ |
| Phase 3 | Motion Primitives（MoveJ / MoveL）| ✅ |
| Phase 4 | Gripper Primitives（Grasp / Release）| ✅ |
| Phase 4.5 | GenesisController（新增）| ✅ |
| Phase 5 | PickAndPlace 迁移 | ✅ |
| Phase 6 | Agent 集成（GUI）| ✅ |
| Phase 7 | 文档和依赖清理 | ⬜ |

**主要遗留项（按优先级）：**
1. Phase 7 文档清理
2. CLI interrupt 处理（`python -m Agent` 路径）
3. MoveL 奇异点检测 / 自适应步长（可选增强）
4. 传送带动态追踪支持（如引入该场景）
