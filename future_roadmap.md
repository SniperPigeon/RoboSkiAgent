# 项目未来进行方向

## Graph优化（简单）

### 当前方向
graph中supervisor增加一个router，当出现模糊的prompt，supervisor无法确定路线时返回请求人类clarification

### 可扩展点

**Clarification interrupt 节点设计**
- 触发条件：supervisor ReAct 循环超过最大步数仍未 saturation，或 T-skills 返回多个候选目标（ambiguous target）
- 节点行为类似 `plan_review`：interrupt + 向操作员展示歧义点 + 接收澄清文本 → 写入 `HumanMessage` 继续 ReAct
- 注意避免死锁：澄清后仍模糊应直接 abort，不能无限循环请求澄清

**消息压缩 / Context Flush 扩展**
- 当前 messages 列表随对话轮次线性增长，supervisor 多轮 ReAct 后 context 会包含大量 ToolMessage 噪音
- 方案：supervisor 完成知识饱和后，用 `RemoveMessage` 批量删除 ReAct 过程中的中间 ToolMessage，只保留最终 `SupervisorOutput` 摘要
- 对应 Checklist 3.5.7（待实现）

**Plan 缓存（可选，较复杂）**
- 对语义相似的历史指令缓存其成功 `todo_list`，下次跳过 supervisor/planner 直接进 plan_review
- 风险：场景变化后缓存失效；建议只做"展示历史计划供操作员参考"而非自动复用

---

## 推理层级复杂化

### 当前方向

**方向一：消除 Skill 抽象层，LLM 直接生成 Primitive 调用序列**
- 当前：Planner → `todo_list[{skill: PickAndPlace, params}]` → Executor 调用 `skill.try_execute()`
- 变更后：Executor 拿到一个高层任务描述，通过 ReAct 循环逐步调用 `MoveJ / MoveL / Grasp / Release` 等 primitive 工具
- 用 `skill.md` 文档代替 `BaseSkill` 的封装约束，LLM 需理解步骤顺序和接近点逻辑
- **优势**：测试 LLM 的长程规划能力和工具调用稳定性；更接近真实 LLM-robot 系统
- **风险**：primitive 级错误恢复更复杂（失败在哪一步？已抓取未放置如何回退？）；需要状态机配合记录执行进度
- 相关论文：**Code as Policies** (Liang et al., 2022) arxiv:2209.07753 — LLM 直接生成 robot 控制代码；**Voyager** (Wang et al., 2023) arxiv:2305.16291 — LLM 自动生成并验证新技能

**方向二：模拟 Depth Camera 做视觉目标识别**
- 当前：target 名称直接作为符号传入（`"Target_A"`），RoboDK 直接 resolve
- 变更后：模拟相机扫描返回候选物体列表（含位置噪声），LLM 需调用"扫描"→"识别"→"确认"工具链，再执行抓取
- 测试点：多目标歧义消解（两个同类物体）、识别失败的 HITL 升级路径
- 实现简化：RoboDK 场景中增加 `scan_workspace()` T-skill 返回物体列表，不需要真实视觉模型

### 可扩展点

**多技能混排场景**
- 补充 `Assemble`（插销）、`Fasten`（拧螺栓）、`Inspect`（检查装配）等 dummy skill，构造需要顺序依赖的 3~5 步任务
- 目标：测试 Planner 在存在 `manual` 任务与 `auto` 任务混排时的指令遵循，以及 Dispatcher 的路由正确性

**失败模式多样化**
- 目前仿真几乎不会真实失败，HITL 路径难以触发
- 方案：在 primitive 中注入可配置的随机失败概率，或增加"碰撞检测"stub 返回 `IK_FAILURE`
- 目的：系统性测试 executor → hitl_handler → retry/replan 全路径

**时序约束规划（较复杂）**
- 某些任务有强先后依赖（螺栓必须先插入才能拧紧），Planner 需生成带依赖关系的 DAG 而非线性队列
- 需要扩展 `todo_list` 数据结构，Dispatcher 需理解依赖边
- 相关论文：**LLM+P** (Liu et al., 2023) arxiv:2304.11477 — LLM 与经典规划器（PDDL）结合，处理约束规划

---

## Agent后训练/Prompt优化APO

### 当前方向
使用 Agent-Lightning 库直接接入现有 agent（无需修改 agent 结构和框架，但本地训练计算量重）

- **奖励信号设计**是核心难题：工业装配 agent 的奖励难以自动化（成功/失败往往需要仿真验证或人工标注）
- 轻量化替代：先用 **离线数据收集** → SFT 微调，再上 RL；避免直接 online RL 的样本效率问题

### Prompt 自动优化（APO）路线（计算量轻，更易落地）

无需训练模型权重，只优化 prompt 文本，适合在当前 Claude API 环境下实验：

- **DSPy** (Khattab et al., 2023) arxiv:2310.03714
  将 prompt 视为可优化参数，用少量标注样本自动搜索最优指令和 few-shot 示例；
  可直接包装现有 supervisor/planner prompt，框架侵入性低
- **OPRO: Large Language Models as Optimizers** (Yang et al., 2023) arxiv:2309.03409
  用 LLM 自身作为优化器，迭代生成更好的 prompt；meta-prompt 驱动，无需梯度
- **Automatic Prompt Engineer (APE)** (Zhou et al., 2022) arxiv:2211.01910
  生成候选 prompt → 评分 → 迭代筛选；适合优化 supervisor 的 knowledge saturation 判断指令

### Agentic RL 路线（计算量重，长期方向）

- **Reflexion** (Shinn et al., 2023) arxiv:2303.11366
  失败后 LLM 生成语言反思（verbal RL），写入 episodic memory，下次避免同类错误；
  **零训练成本**，可直接在现有 hitl_handler replan 路径上实验——让 LLM 总结失败原因并注入下一轮 supervisor context
- **LATS: Language Agent Tree Search** (Zhou et al., 2023) arxiv:2310.04406
  将 MCTS 应用于 agent 决策，每步扩展多个候选 action → 仿真评估 → 回溯选优；
  适合 Planner 生成多个候选计划并评分
- **Agent Lightning** arxiv:2508.03680（已列，见下方）

## 论文阅读：

### 基础LLM训练：

主线脉络：Transformer → 预训练范式（GPT系列）→ Scaling Laws → 对齐/指令微调 → 推理增强（CoT / MoE）→ RL后训练（RLHF → GRPO / Deepseek R1）

#### 1. Transformer 基础
- **Attention Is All You Need** (Vaswani et al., 2017) arxiv:1706.03762
  Self-attention + Position Encoding + Encoder-Decoder 架构奠基，后续所有 LLM 的结构基础

#### 2. 预训练范式：GPT 系列
- **GPT-1: Improving Language Understanding by Generative Pre-Training** (Radford et al., 2018)
  Decoder-only 单向 LM 预训练 + fine-tune，确立"预训练→下游任务"范式
- **GPT-2: Language Models are Unsupervised Multitask Learners** (Radford et al., 2019)
  Zero-shot 泛化涌现；首次展示规模带来的任务无关能力
- **GPT-3: Language Models are Few-Shot Learners** (Brown et al., 2020) arxiv:2005.14165
  175B 参数 + In-Context Learning，few-shot 无需梯度更新；提出 prompt engineering

#### 3. Scaling Laws
- **Scaling Laws for Neural Language Models** (Kaplan et al., 2020) arxiv:2001.08361
  Loss 与模型参数/数据量/计算量之间的幂律关系；"更大就是更好"的理论依据
- **Chinchilla: Training Compute-Optimal Large Language Models** (Hoffmann et al., 2022) arxiv:2203.15556
  修正 Kaplan 结论：给定 FLOP 预算，模型与数据应同比例增长；Chinchilla-optimal 成为此后训练基准

#### 4. 指令对齐 / RLHF
- **InstructGPT: Training language models to follow instructions with human feedback** (Ouyang et al., 2022) arxiv:2203.02155
  SFT → 奖励模型 → PPO 三阶段 RLHF 流程；ChatGPT 的直接前身
- **FLAN: Finetuned Language Models Are Zero-Shot Learners** (Wei et al., 2021) arxiv:2109.01652
  指令微调（Instruction Tuning）提升零样本泛化，是 RLHF 之前的对齐路线

#### 5. 推理增强：Chain-of-Thought
- **Chain-of-Thought Prompting Elicits Reasoning in Large Language Models** (Wei et al., 2022) arxiv:2201.11903
  "Let's think step by step"；中间推理步骤大幅提升复杂任务准确率
- **Self-Consistency Improves Chain of Thought Reasoning** (Wang et al., 2022) arxiv:2203.11171
  多路采样取多数投票，比贪心解码更稳健
- **Tree of Thoughts** (Yao et al., 2023) arxiv:2305.10601
  将推理建模为树搜索（BFS/DFS），可回溯；CoT 的泛化形式
- **ReAct: Synergizing Reasoning and Acting in Language Models** (Yao et al., 2022) arxiv:2210.03629
  交替生成推理轨迹（Thought）和工具调用（Act），Agent 领域核心方法

#### 6. 稀疏专家：MoE
- **Outrageously Large Neural Networks: Sparsely-Gated MoE** (Shazeer et al., 2017) arxiv:1701.06538
  门控稀疏激活，在参数量翻倍时计算量不变；MoE 机制的工程化基础
- **Switch Transformers: Scaling to Trillion Parameter Models** (Fedus et al., 2021) arxiv:2101.03961
  简化 Top-1 路由，将 MoE 稳定应用于 T5 规模；激活参数 vs 总参数的经典分析
- **Mixtral of Experts** (Mistral AI, 2024) arxiv:2401.04088
  Top-2 MoE，开源高性价比 MoE 模型；8×7B 激活 2 个专家，对标 LLaMA-2 70B

#### 7. RL 后训练：从 PPO 到 GRPO
- **PPO: Proximal Policy Optimization Algorithms** (Schulman et al., 2017) arxiv:1707.06347
  Clip 目标函数控制策略更新幅度；RLHF 中最常用的在线 RL 算法
- **DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning** (DeepSeek-AI, 2025) arxiv:2501.12948
  纯 RL（GRPO）从 base model 直接训练出 CoT 推理能力，无需监督 CoT 数据；
  Group Relative Policy Optimization（GRPO）去除 Critic 模型，用组内相对奖励估计优势函数
- **GRPO: DeepSeekMath** (Shao et al., 2024) arxiv:2402.03300
  GRPO 算法原始来源（数学推理场景），是 R1 使用的 RL 算法的前置论文

#### 补充：开源基座（工程实践参考）
- **LLaMA 2** (Touvron et al., 2023) arxiv:2307.09288
  开源高质量基座；SFT + RLHF 训练细节公开，是本地 LLM 研究的参照基准
- **DeepSeek-V3** (DeepSeek-AI, 2024) arxiv:2412.19437
  MLA（Multi-head Latent Attention）+ MoE，671B 总参数 / 37B 激活；
  FP8 混合精度 + 流水线并行，训练成本压缩至同规模模型 1/10

---

### Agentic Training：

#### Agent 框架与工具使用
- **ReAct: Synergizing Reasoning and Acting in Language Models** (Yao et al., 2022) arxiv:2210.03629
  *(已在基础 LLM 部分列出，但对本项目最直接相关，建议优先阅读)*
  本项目 supervisor/executor 的 ReAct 循环直接基于此范式
- **Toolformer: Language Models Can Teach Themselves to Use Tools** (Schick et al., 2023) arxiv:2302.04761
  自监督学习工具调用：LLM 自己决定何时插入 API 调用并验证其有用性；
  与本项目不同处：Toolformer 是训练时嵌入工具，本项目是推理时 function calling
- **Gorilla: Large Language Model Connected with Massive APIs** (Patil et al., 2023) arxiv:2305.15334
  专门训练 API 调用能力，引入 AST 匹配评估调用正确性；
  与本项目 skill registry + tool schema 生成直接相关

#### Agentic RL 训练
- **Agent Lightning: Train ANY AI Agents with Reinforcement Learning** arxiv:2508.03680
  无需修改 agent 结构，通过 trajectory 采样 + RL 直接优化现有 agent 行为
- **ArCHer: Training Language Model Agents via Hierarchical Multi-Turn RL** arxiv:2402.19446
  分层 RL：高层 agent 分解目标，低层 agent 执行；多轮交互下的信用分配问题；
  *(原编号 402.19446 有误，已修正为 2402.19446)*
- **Reflexion: Language Agents with Verbal Reinforcement Learning** (Shinn et al., 2023) arxiv:2303.11366
  *(已在 APO 部分详述，零计算成本的 RL 替代方案)*

#### 机器人 + LLM（具身智能方向）
- **SayCan: Do As I Can, Not As I Say** (Ahn et al., 2022) arxiv:2204.01691
  用机器人技能的可执行概率对 LLM 生成的计划进行 affordance 重排序；
  解决 LLM 生成"物理上不可行"的计划问题——与本项目 `require_robot_active` 守卫理念相通
- **Code as Policies** (Liang et al., 2022) arxiv:2209.07753
  *(已在推理层级部分列出)*
  LLM 生成 Python 代码直接控制机器人；对应"消除 Skill 抽象层"方向的参考实现
- **Voyager: An Open-Ended Embodied Agent with Large Language Models** (Wang et al., 2023) arxiv:2305.16291
  *(已在推理层级部分列出)*
  自动生成技能库并验证；对应动态 skill 合成方向

#### Agent 评估与 Benchmark（用于衡量改进效果）
- **AgentBench: Evaluating LLMs as Agents** (Liu et al., 2023) arxiv:2308.03688
  多环境 agent 能力评估基准（OS、DB、Web、游戏等）；提供 agent 能力量化方法论
- **τ-bench: A Benchmark for Tool-Agent-User Interaction** (Yao et al., 2024) arxiv:2406.12045
  工具调用 + 多轮用户交互评估；与本项目 HITL 交互路径设计高度相关