# 用 AgentLightning APO 自动优化 Planner 提示词

> 本文记录如何用 AgentLightning 的 Automated Prompt Optimization（APO）算法自动迭代机器人装配规划器（Planner）的系统提示词。代码在 `trainer/apoptimizer/` 目录下。

---

## 背景：我们在优化什么

`RoboSkiAgent` 的 Planner 节点负责把自然语言装配指令翻译成 `todo_list`（一组 skill-level 任务）。其行为的核心由 `Agent/prompts/planner.txt` 这份系统提示词决定。

传统做法是人工调提示词、跑、看效果、再改。APO 把这个循环自动化：
1. 以 `planner.txt` 为种子，在训练数据上评分
2. 用 LLM 分析哪些 rollout 表现差、哪些好，生成「文本梯度」（critique）
3. 用另一个 LLM 把 critique 应用到提示词上，得到新候选版本
4. 在验证集上评估所有候选，保留得分最高的 `beam_width` 个
5. 重复若干轮（`beam_rounds`），最终返回历史最优提示词

---

## 组件一：数据集与加载器

**文件：** `planning_agent_apo.py`

### 数据格式

数据集是 JSONL 文件，每行一个 `PlannerTask`：

```json
{"task_id": "collect_0001", "plan_input": "把第一个 Part A 放到目标位置", "expected": [{"task_id": "t1", "type": "auto", "skill": "PickAndPlace", "params": {...}}]}
```

三个字段：
- `plan_input`：给 planner 的自然语言指令（训练输入）
- `expected`：专家（Claude Sonnet）生成并验证过的参考 `todo_list`（用于 critic 打分）
- `task_id`：标识符

### 数据收集

`planning_agent.py` 中的 `collect_dataset()` 用当前 `planner.txt` 批量跑 Planner，结构验证通过后写 JSONL：

```python
collect_dataset(
    instructions=[...],
    output_path="plan_claude.jsonl",
    skip_invalid=True,   # reject tasks with unknown skills or empty params
)
```

`_validate_todo_list()` 会检查：技能名是否在 `SkillMdLoader` 里、auto 任务 params 不为空、manual 任务 description 不为空。

### 加载与分割

```python
samples = load_dataset()                     # reads plan_claude.jsonl
train, val = split_dataset(samples,
    val_ratio=0.25,   # 75% train, 25% val
    seed=42,          # deterministic shuffle
)
```

`split_dataset` 用 `random.Random(seed).shuffle()` 保证每次运行分割结果一致，方便对比实验。返回值被 `cast` 成 `agl.Dataset[PlannerTask]`（其实就是 `list`，cast 仅为类型提示）。

---

## 组件二：PromptTemplate（优化目标）

**文件：** `agentlightning/types/resources.py`，种子来自 `Agent/prompts/planner.txt`

```python
def get_initial_planner_prompt_template() -> agl.PromptTemplate:
    raw = (_PROMPTS_DIR / "planner.txt").read_text(encoding="utf-8")
    return agl.PromptTemplate(template=raw, engine="f-string")
```

`PromptTemplate` 是 APO 的优化对象，有三个字段：
- `template`：提示词文本（APO 会在这上面做修改）
- `engine`：渲染引擎，`"f-string"` 意味着用 `template.format(**task)` 渲染
- `resource_type`：固定为 `"prompt_template"`，用于反序列化时做 discriminated union

> **重要设计决定**：`planner.txt` 只包含 Planner 的基础规则（base rules）。技能参考列表（Available Skills）由 `planner_v2.py` 在运行时动态追加，**不是优化目标的一部分**。这样优化出的提示词与具体场景解耦，换装配站点无需重新优化。

在 rollout 执行时，APO 把当前候选提示词渲染后注入 Planner：

```python
planner_prompt = prompt_template.format(**task)   # f-string, task 字段可按需插值
planner_v2 中：
    base_rules = _prompt_override.get()           # ContextVar 注入，线程隔离
```

---

## 组件三：Agent 本体（Rollout 函数）

**文件：** `planning_agent.py`

### LangGraph 规划图

```
START → supervisor → planner_v2 → END
```

- **supervisor**：用 T-skills 查询场景（`list_objects`, `list_targets` 等），输出结构化指令给 planner
- **planner_v2**：根据 supervisor 输出动态调用 `add_<SkillName>_task` 工具，构建 `todo_list`
- 两个节点都用 `ChatAnthropic`（通过 `create_llm()`，受 `ROBOSKI_LLM_PROVIDER` 控制）

图是懒惰初始化的单例（在 `ROBOSKI_LLM_PROVIDER` 变化时自动重建）：

```python
def _get_graph() -> Any:
    load_dotenv(override=True)                   # re-read .env every call
    provider = os.getenv("ROBOSKI_LLM_PROVIDER", "claude")
    if _graph is None or provider != _graph_provider:
        _graph = build_planning_graph()          # rebuild if provider changed
    return _graph
```

### planner_agent()

```python
def planner_agent(human_input: str, planner_prompt: str) -> list[dict]:
    graph  = _get_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}  # isolated per call
    reset_supervisor_cache()

    token = _prompt_override.set(planner_prompt)   # thread-safe injection
    try:
        graph.invoke(make_initial_state(human_input), config=config)
    except Exception as e:
        logger.error("[planner_agent] graph failed: %s", e)
        return []
    finally:
        _prompt_override.reset(token)              # always restore

    return graph.get_state(config).values.get("todo_list", [])
```

并发安全保证：
- 每次调用用独立的 `thread_id`，checkpointer 状态完全隔离
- `_prompt_override` 是 `ContextVar`，设置只影响当前线程/asyncio task

---

## 组件四：Rollout Wrapper（@agl.rollout）

**文件：** `planning_agent.py`，装饰器定义在 `agentlightning/litagent/decorator.py`

```python
@agl.rollout
def planner_rollout(task: PlannerTask, prompt_template: agl.PromptTemplate) -> float:
    planner_prompt = prompt_template.format(**task)
    todo_list      = planner_agent(task["plan_input"], planner_prompt)
    reward         = critic_score(task["plan_input"], task["expected"], todo_list)
    logger.info("[rollout] task=%s  plan=%d tasks  reward=%.2f",
                task["task_id"], len(todo_list), reward)
    return reward
```

`@agl.rollout` 会检查函数签名，识别出 `(task, prompt_template)` 匹配 **`prompt_rollout`** 模式，把函数包装成 `FunctionalLitAgent`。

当 APO 评估某个候选提示词时，它把当前版本的 `PromptTemplate` 注入 `resources` 字典，runner 自动取出第一个 `PromptTemplate` 类型的资源传给 `prompt_template` 参数。

每次 rollout 的执行顺序：
1. `prompt_template.format(**task)` → 渲染完整 planner 系统提示词
2. `planner_agent(instruction, prompt)` → 跑 LangGraph → 得到 `todo_list`
3. `critic_score(instruction, expected, todo_list)` → GPT-4.1-mini 评分 → `[0.0, 1.0]`
4. 返回 reward，AgentOps tracer 自动收集沿途所有 LLM span

---

## 组件五：Critic（奖励信号）

**文件：** `planning_agent.py`

Critic 是 APO 的奖励函数，用 OpenAI GPT-4.1-mini 把模型生成的 `todo_list` 与专家参考对比打分：

```python
_CRITIC_SYSTEM = """
Score the actual plan on a scale from 0.0 to 1.0:
  1.0 — identical or semantically equivalent
  0.7 — correct skills and order, minor param differences
  0.4 — partially correct
  0.0 — completely wrong or empty

Respond with ONLY a JSON object: {"score": <float>, "reason": "<one sentence>"}
"""
```

```python
def critic_score(plan_input, expected, actual) -> float:
    response = _get_critic_client().chat.completions.create(
        model="gpt-4.1-mini",
        max_tokens=256,
        messages=[
            {"role": "system", "content": _CRITIC_SYSTEM},
            {"role": "user",   "content": json.dumps({
                "instruction": plan_input,
                "expected":    expected,
                "actual":      actual,
            })},
        ],
    )
    text = response.choices[0].message.content
    return max(0.0, min(1.0, float(json.loads(text)["score"])))
```

两个设计要点：
- **任何异常都 catch，返回 0.0**：单次 critic 失败不会崩溃整个训练循环，只是这条 rollout 得 0 分
- **Critic 与 APO 的梯度引擎独立**：Critic（同步 OpenAI）负责生成奖励信号；APO 的梯度/编辑模型（异步 OpenAI）负责优化提示词，两者互不干扰

> **为什么用 GPT 而不是 Claude？**
> AgentOps 对 Anthropic SDK 的 tracing 实现里，工具定义被序列化为一个扁平 JSON 字符串存入 `gen_ai.request.functions`，而 `TraceToMessages` adapter 期望的是分层的点分属性（`gen_ai.request.functions.0.name` 等）。为规避这个兼容性问题，梯度/编辑引擎直接用 OpenAI，让 OpenAI 的 span 格式通过 adapter。

---

## 组件六：Logger 注册

**文件：** `planning_agent_apo.py`

APO 所有进度日志走 Python `logging`（logger 名 `agentlightning`），默认级别 WARNING，不配置就看不到任何输出。

```python
def setup_apo_logger(file_path: str = "apo.log") -> None:
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(file_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    apo_logger = logging.getLogger("agentlightning")
    apo_logger.setLevel(logging.INFO)
    apo_logger.addHandler(console_handler)
    apo_logger.addHandler(file_handler)
```

注意：`setup_apo_logger()` 必须在 `Trainer` 和 `APO` 实例化**之前**调用，否则初始化日志已经丢失。

日志里能看到的关键信息：
- `Seed prompt baseline score: 0.xxx` — 优化前的基线分
- `Round 1/3...` — 当前轮次
- `[critic] score=0.xx  reason=...` — 每次 rollout 的评分
- `New best prompt found (score: 0.xxx)` — 发现更好的提示词

---

## 组件七：APO 算法（Beam Search）

**文件：** `agentlightning/algorithm/apo/apo.py`

APO 是带文本梯度的 beam search，基于 [ProTeGi](https://aclanthology.org/2023.emnlp-main.494.pdf) 论文。

### 核心参数

```python
apo = agl.APO(
    async_openai_client=AsyncOpenAI(),   # for gradient & edit calls
    gradient_model="gpt-4.1-mini",       # LLM for generating critiques
    apply_edit_model="gpt-4.1-mini",     # LLM for applying edits to prompt
    beam_width=2,       # keep top-2 prompts per round
    branch_factor=2,    # generate 2 new candidates from each parent
    beam_rounds=3,      # 3 rounds of optimization
)
```

### 一轮 Beam Search 的详细流程

```
当前 beam = [prompt_A (score=0.6), prompt_B (score=0.5)]
                        ↓
    sample_parent_prompts() → [A, B]（随机采样 beam_width 个）
                        ↓
    对每个 parent，生成 branch_factor=2 个新候选:
      1. 在训练集上跑 gradient_batch_size=4 个 rollout
      2. compute_textual_gradient():
         - 把 rollout 结果（消息历史 + reward）发给 gradient_model
         - 随机选一个 gradient prompt 模板
         - 返回 critique（"哪里做对了，哪里做错了"）
      3. textual_gradient_and_apply_edit():
         - 把 critique + 当前提示词发给 apply_edit_model
         - 随机选一个 apply_edit 模板
         - 返回修改后的新提示词 → new candidate
                        ↓
    candidates = [A, B, new_A1, new_A2, new_B1, new_B2]（共 6 个）
                        ↓
    在验证集上评估所有候选，取平均 reward
                        ↓
    beam = top_2_by_score(candidates)  →  [new_A1, new_B2]
                        ↓
    更新 _history_best_prompt（如果有候选超过历史最高分）
```

### 为什么需要 TraceToMessages

APO 的梯度计算需要把「rollout 过程」传给 LLM 分析。具体来说：

1. 每次 `planner_agent` 跑图时，AgentOps tracer 把所有 LLM 调用记录为 OpenTelemetry span
2. Span 存在 store 里，属性格式为 `gen_ai.prompt.0.role`, `gen_ai.completion.0.content` 等
3. APO 查询这些 span，调用 `adapter.adapt(spans)` → `List[OpenAIMessages]`
4. `OpenAIMessages` 是标准的 OpenAI chat 格式，可以直接嵌入 gradient prompt 模板，让 GPT 看到完整对话历史来生成 critique

---

## 组件八：Trainer 与主函数流程

**文件：** `planning_agent_apo.py`

### Trainer 参数

```python
trainer = Trainer(
    algorithm=apo,
    initial_resources={"planner_prompt": make_initial_prompt()},
    adapter=TraceToMessages(),   # required for APO (not the default TracerTraceToTriplet)
    n_runners=2,                 # 2 parallel rollout workers
)
```

- `initial_resources`：key 名（`"planner_prompt"`）要与 rollout 函数签名中的 `prompt_template` 参数对应——APO 自动找 `NamedResources` 里第一个 `PromptTemplate` 类型的值注入
- `adapter=TraceToMessages()`：**必须显式指定**，Trainer 默认用 `TracerTraceToTriplet`，APO 需要 `TraceToMessages`
- `n_runners`：并行 worker 数，每个 worker 独立跑 rollout，共享同一个 store

### 完整 main() 流程

```python
def main():
    load_dotenv()
    setup_apo_logger()                          # ① 先注册 logger，不然看不到任何输出

    train, val = split_dataset(load_dataset())  # ② 加载并分割数据集
    print(f"Dataset: {len(train)} train / {len(val)} val")

    openai_client = AsyncOpenAI()               # ③ APO 梯度/编辑引擎（reads OPENAI_API_KEY）

    apo = agl.APO(...)                          # ④ 配置 beam search 超参
    trainer = Trainer(...)                      # ⑤ 连接 APO + 数据 + runner

    try:
        trainer.fit(                            # ⑥ 启动训练
            planner_rollout,
            train_dataset=train,
            val_dataset=val,
        )
    except KeyboardInterrupt:
        print("\n[APO] Interrupted.")
    finally:
        _print_best_prompt(apo)                 # ⑦ 无论正常结束还是 Ctrl+C 都打印最优提示词
```

### _print_best_prompt()

```python
def _print_best_prompt(apo) -> None:
    try:
        best = apo.get_best_prompt()
        print(f"BEST PROMPT (score={apo._history_best_score:.3f}):\n{best.template}")
        out = _HERE / "best_planner_prompt.txt"
        out.write_text(best.template, encoding="utf-8")
        print(f"Saved to {out}")
    except ValueError:
        print("\n[APO] No best prompt recorded yet.")
```

APO 内部维护 `_history_best_prompt`（`PromptTemplate`）和 `_history_best_score`（float），每轮验证后更新。`get_best_prompt()` 直接返回这个对象；若 `run()` 尚未开始则抛 `ValueError`。

---

## 系统整体数据流

```
JSONL 文件
    ↓ load_dataset()
list[PlannerTask]
    ↓ split_dataset()
train / val
    ↓ trainer.fit()
┌─────────────────────────────────────────────────────────┐
│  APO.run(train, val)                                    │
│    ↓ evaluate_prompt_on_batch()                         │
│    ↓ store.enqueue_rollout(task, resources_id)          │
│                                                         │
│  Runner(s) × n_runners                                  │
│    ↓ planner_rollout(task, prompt_template)             │
│      ↓ planner_agent(plan_input, rendered_prompt)       │
│        ↓ graph.invoke()  [Anthropic ChatAnthropic]      │
│          supervisor → planner_v2                        │
│          [AgentOps traces all LLM calls]                │
│      ↓ critic_score(plan_input, expected, todo_list)    │
│        [GPT-4.1-mini, sync OpenAI]                      │
│      ↓ return reward [0.0, 1.0]                         │
│                                                         │
│  APO.compute_textual_gradient()                         │
│    ↓ store.query_spans(rollout_id)                      │
│    ↓ TraceToMessages.adapt(spans) → OpenAIMessages      │
│    ↓ gpt-4.1-mini → critique text                       │
│                                                         │
│  APO.textual_gradient_and_apply_edit()                  │
│    ↓ gpt-4.1-mini → new PromptTemplate.template         │
│                                                         │
│  APO._evaluate_and_select_beam()                        │
│    ↓ keep top beam_width candidates                     │
│    ↓ update _history_best_prompt if improved            │
└─────────────────────────────────────────────────────────┘
    ↓ trainer.fit() 返回 / KeyboardInterrupt
_print_best_prompt(apo)
    ↓ save best_planner_prompt.txt
```

---

## 常见问题与注意事项

### 1. 看不到日志输出

`setup_apo_logger()` 必须在 `Trainer` 实例化前调用。APO 所有进度信息都是 `logging.INFO` 级别，不调用就全部被过滤。

### 2. 更换 LLM Provider 不生效

`_get_graph()` 每次调用都会 `load_dotenv(override=True)`，然后检查 `ROBOSKI_LLM_PROVIDER` 是否变化。如果只是修改了 `.env` 而没有重新运行脚本，需要确认 `override=True` 生效。

### 3. 训练途中想查看当前最优

`Ctrl+C` 触发 `KeyboardInterrupt`，`finally` 块会调用 `_print_best_prompt(apo)`，打印并保存到 `best_planner_prompt.txt`。

### 4. TraceToMessages 与 Anthropic 的兼容性

AgentOps 的 Anthropic 驱动会把工具定义列表序列化为一个扁平 JSON 字符串存入 `gen_ai.request.functions`：

```python
# agentops/.../anthropic/attributes/tools.py
attributes[SpanAttributes.LLM_REQUEST_FUNCTIONS] = json.dumps(tool_names)
# result: "gen_ai.request.functions" = '["add_PickAndPlace_task", ...]'
```

而 `TraceToMessages` 期望的是 OpenAI instrumentation 风格的分层属性（`gen_ai.request.functions.0.name`）。遇到此错误时表现为：

```
TypeError: string indices must be integers, not 'str'
  at: "name": fn["name"]
```

当前用 OpenAI GPT 跑 APO 梯度计算时，span 由 OpenAI 的 instrumentation 记录，格式正确，不触发此问题。

### 5. beam_width 与 branch_factor 的数学关系

每轮生成的候选数量 = `beam_width × branch_factor`。全部候选 + 当前 beam 中的父 prompt 一起在验证集评估，取 top-`beam_width` 作为下一轮的 beam。

`beam_width=2, branch_factor=2`：每轮 4 个新候选 + 2 个父 = 共 6 个参与评估，保留 2 个。

### 6. val_dataset 的批次消费

APO 内部用 `batch_iter_over_dataset(val_dataset, val_batch_size=16)` 创建**有状态的迭代器**，每轮消费一批。如果 val_dataset 太小（< val_batch_size），部分批次会只包含重复样本或比预期少。建议 `len(val) ≥ val_batch_size`。

---

## 超参调整参考

| 参数 | 当前值 | 说明 |
|------|--------|------|
| `beam_width` | 2 | 增大可提升多样性，但验证开销线性增加 |
| `branch_factor` | 2 | 每个父 prompt 产生的变体数 |
| `beam_rounds` | 3 | 优化轮次，资源充裕时可增至 5–10 |
| `gradient_batch_size` | 4（默认）| 计算梯度时采样的 rollout 数 |
| `val_batch_size` | 16（默认）| 每轮验证样本数，建议 ≥ val 集大小 |
| `n_runners` | 2 | 并行 rollout worker，受限于 RoboDK 连接数 |
| `val_ratio` | 0.25 | 验证集比例，4 个样本 → 1 val, 3 train |

---

*最后更新：2026-04-16*
