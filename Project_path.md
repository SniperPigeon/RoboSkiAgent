# 未来Project方向

## 最简易：图优化

- 增加supervisor对于vague requests的反馈，请求人类clarification。

> **[补充细节]**
> 当前Supervisor已有 `request_human_intervention` 白名单接口（`@require_robot_active(bypass_halt=True)`），可直接复用为clarification触发点。
> - **触发条件**：Supervisor在metatool查询后仍存在歧义（如 `list_targets()` 返回多个匹配名），通过新增T-skill `ask_clarification(question: str)` 将问题写入 `execution_log` 并设 `halt_flag=True`、`halt_reason="CLARIFICATION"`
> - **resume路径**：新增 `clarification_handler` interrupt节点，操作员输入答案后写入 `HumanMessage`，Supervisor继续ReAct循环
> - **注意**：不要滥用clarification兜底——Supervisor应先穷尽metatool查询（`check_item_exists`、`list_objects`等），仅在信息真正缺失时才中断；过度clarification等于把不确定性转移给人，违反Golden Rule 5

---

## Reasoning检验：复杂化

移除Skills这一层抽象（比如Pick And Place），LLM直接操作底层primitives（例如MoveL, MoveJ, Grasp），skill转化为prompt（skills.md），检验指令遵循能力
- Supervisor, planner职责清晰化：Supervisor概括，Planner形成更长的primitive链条

> **[补充细节]**
> 这个方向改动的核心是将 `SkillRegistry.get_tools()` 替换为 `PrimitiveRegistry.get_tools()`，向Planner暴露 `MoveJ_try_execute / MoveL_try_execute / Grasp_try_execute / Release_try_execute` 四个工具。
>
> **架构层面需要解决的问题：**
> 1. **坐标层泄漏风险**：Primitive的 `try_execute` 参数含 `ref_frame: Optional[Mat]`，是Python对象，LLM无法序列化。需要在此方向下新增一层"符号化primitive wrapper"，只暴露字符串参数（target_name: str），在wrapper内部resolve。这实际上是重新发明了Skill层，但更薄。
> 2. **计划长度爆炸**：PickAndPlace有10个步骤，直接展开后一个装配任务的 `todo_list` 可能达到30-50条primitive记录。Dispatcher和Executor的循环需要验证在此规模下不退化。
> 3. **check()粒度问题**：Primitive的 `check()` 只从当前位置验证下一步，不做全路径仿真。Planner生成的primitive链条存在中间步失败的风险，LLM需要在Executor ReAct循环里逐步check—这实际上是把Skill.check()的全路径验证逻辑下放给了LLM推理，测试的正是这个推理能力。
> 4. **评估指标**：与当前Skill-level基线对比，记录：任务完成率、HITL触发次数、Planner生成的primitive链错误率（顺序错误/参数错误/目标名幻觉）

---

## Agentic post-training

接入Microsoft的开源框架，探索一下agentic下的RL post-training，例如GPRO

- 需要先读一下之前的论文：Transformer -> GPT-3 -> Deepseek R1训练等。待补充

> **[补充细节]**
>
> ### 论文阅读路径（顺序）
> 1. **Attention is All You Need** (Vaswani et al., 2017) — Transformer基础，理解self-attention、positional encoding
> 2. **GPT-3** (Brown et al., 2020) — 大规模language model，few-shot prompting能力来源
> 3. **InstructGPT** (Ouyang et al., 2022) — RLHF基础：SFT → Reward Model → PPO三阶段
> 4. **PPO** (Schulman et al., 2017) — 理解clip objective，为什么比REINFORCE稳定
> 5. **DeepSeek-R1** (DeepSeek-AI, 2025) — GRPO算法：用group relative advantage代替value network；Group内G个rollout对比排名，无需训练独立critic
> 6. **（可选）RLVR综述** — Reinforcement Learning from Verifiable Rewards，robotic/math领域的verifiable reward设计模式
>
> ### 框架选型说明
> "Microsoft的开源框架"可能指 **DeepSpeed**（Microsoft开源，用于分布式训练加速，不含GRPO算法本身）。
> GRPO的实际实现框架推荐：
> - **HuggingFace TRL**（`trl` 库，`GRPOTrainer`）：最易上手，与HuggingFace模型生态无缝衔接
> - **veRL**（ByteDance）：大规模并行rollout，适合需要大量仿真episode的机器人场景
> - **OpenRLHF**：支持多种RL算法，包括GRPO
>
> 如果确实想用Microsoft生态，可以用 **DeepSpeed** 作为训练后端，配合TRL的GRPO算法。
>
> ### 关键设计问题：Reward Function
>
> 这是RL在本项目中最核心也最难的问题。现有架构已经内置了天然的verifiable reward信号：
>
> | 信号来源 | 字段 | 类型 | 说明 |
> |---------|------|------|------|
> | 任务成功 | `SkillResult.success` | binary | 最强信号，episode级别 |
> | 需人工介入 | `SkillResult.needs_hitl` | binary | 效率惩罚：触发HITL = 负奖励 |
> | 执行阶段 | `SkillResult.execution_phase` | categorical | 失败在VALIDATION vs PLANNING vs EXECUTION，给不同惩罚 |
> | 执行日志 | `GlobalState.execution_log` | list[str] | 完整轨迹，可用于构造dense reward |
> | 错误类型 | `SkillResult.error_type` | str | `IK_FAILURE` / `COLLISION` / `TIMEOUT`，可按难度差异化奖励 |
>
> 建议的reward设计（以Executor/Planner为训练对象）：
> ```
> r = +1.0  (task success, SkillResult.success=True)
>     -0.5  (per HITL escalation)
>     -0.2  (per VALIDATION failure — LLM参数错误，可避免)
>     -0.1  (per PLANNING failure — IK/Collision，部分可避免)
>     -0.0  (EXECUTION failure — 硬件/超时，难以预测，不惩罚LLM)
> ```
>
> ### 训练哪个组件？
>
> - **Planner**（推荐起点）：给定Supervisor输出的场景描述，生成 `todo_list`；reward = 整个任务完成率 + HITL次数。输入输出结构固定，适合GRPO的G组rollout对比。
> - **Executor ReAct**：给定失败的 `SkillResult`，决定retry参数或escalate；reward = 最终是否recover成功。训练难度更高，需要更长context。
> - **Supervisor**：最难，reward定义模糊（"问题消除"很难量化）。不建议作为起点。
>
> ### 与Genesis平台的依赖关系
>
> GRPO需要大量rollout（每个prompt生成G=8~16个候选轨迹）。RoboDK仿真速度是瓶颈：
> - RoboDK：~1-5 rollout/s（单进程，依赖外部进程通信）
> - Genesis GPU仿真：~1000+ rollout/s（并行）
>
> **结论：Agentic RL post-training实际上依赖"更换模拟平台"先完成**，或者退而求其次，用**离线RL**（在已收集的成功/失败轨迹上做监督式policy improvement，无需在线rollout）。
>
> ### GRPO在本场景的适用性分析
>
> GRPO的核心优势是不需要value network（critic），只用group内relative ranking给advantage。对于robotic assembly：
> - ✅ Reward是verifiable的（`SkillResult.success` 是真实物理结果，不需要学习reward model）
> - ✅ 不需要PPO的value network，参数量更少
> - ⚠️ Episode较长（10步PickAndPlace），credit assignment是挑战——建议加intermediate reward
> - ⚠️ 仿真reset cost高（每个episode结束需要重置机器人和工件状态）

---

## 更换模拟平台

RoboDK闭源且需要license目前项目给不到，用genesis开源替代还可以探索别的task。例如带上conveyor belt。

> **[补充细节]**
>
> ### Genesis平台特点
> - **Genesis**（genesis-embodied-ai/genesis，MIT License）：基于Python + GPU加速的物理仿真框架，支持刚体/流体/软体统一仿真
> - 核心优势：支持数千个并行环境（parallel worlds），专为RL训练设计，仿真速度远超RoboDK
> - 缺点：生态不如Isaac Lab成熟，机器人URDF导入和控制器配置需要手动工作
>
> ### 对现有架构的影响
> 本项目的平台无关架构设计（SkiLib中 `BasePrimitive` 与 `BaseSkill` 分层）使得切换平台的改动最小化：
> - **只需重写**：`SkiLib/primitives/motion.py` 和 `SkiLib/primitives/gripper.py`（四个类：MoveJ / MoveL / Grasp / Release）
> - **无需改动**：`BaseSkill` 层、`SkillRegistry`、整个 `Agent/` 编排层、`GlobalState`、所有LangGraph节点
> - **需要适配**：`RobotContext` 初始化（目前硬绑定 `robolink.Robolink()`），需要改为平台可选
>
> ### 扩展任务可能性
> | 任务 | 难度 | 说明 |
> |------|------|------|
> | Conveyor Belt Pick | 中 | 需新增 `GetConveyorItem` T-skill，Supervisor查询传送带上的工件 |
> | Multi-Robot协作 | 高 | 两台机器人协同，需扩展 `GlobalState.robot_state` 为列表 |
> | 视觉引导装配 | 高 | 需接入相机primitive，Supervisor可调用 `detect_object(camera_id)` |
>
> ### 与RL方向的关系
> Genesis的并行仿真是Agentic RL post-training的前提条件（见上方RL章节分析）。建议将此方向作为RL方向的基础设施先行完成。

