# 迁移方案：graph_test.ipynb → Agent/ Python 包

## Context

`Agent/notebooks/graph_test.ipynb` 中已完成 LangGraph 状态机的全部节点实现（Supervisor、Planner、PlanReview、Dispatcher、Executor、ManualHandler、HITLHandler），但 notebook 形式存在全局变量耦合、无法独立运行、prompt 硬编码等问题。需要迁移为可独立运行的 Python 包，同时保持 GUI 可选、prompt 可编辑、RoboDK 初始化显式可控。

---

## 目标目录结构

```
Agent/
├── __init__.py                   # re-export build_graph, GlobalState
├── state.py                      # GlobalState TypedDict
├── llm.py                        # create_llm() 工厂，读环境变量
├── graph.py                      # build_graph(llm, checkpointer) → CompiledStateGraph
├── __main__.py                   # CLI 入口：python -m Agent "指令"
├── prompts/                      # 纯文本 prompt 模板，{placeholder} 格式
│   ├── supervisor.txt
│   ├── planner.txt
│   └── executor.txt
├── nodes/
│   ├── __init__.py
│   ├── supervisor.py             # SupervisorOutput, supervisor(), 依赖 llm
│   ├── planner.py                # AutoTask/ManualTask/PlannerOutput, planner(), 依赖 llm
│   ├── plan_review.py            # plan_review(), plan_review_router() — 纯中断逻辑
│   ├── dispatcher.py             # dispatcher(), task_router() — 纯代码
│   ├── manual_handler.py         # manual_intervention_handler/router — 纯中断逻辑
│   ├── executor.py               # _EscalateHITLException, executor(), post_task_router(), 依赖 llm
│   └── hitl_handler.py           # hitl_handler(), hitl_router() — 纯中断逻辑
├── gui.py                        # Gradio UI（可选，graph 零感知 Gradio）
└── notebooks/                    # 保留，仅作参考
    └── graph_test.ipynb
```

---

## 关键设计决策

### 1. LLM 注入：`functools.partial`

notebook 中 `llm` 是全局变量，所有节点通过闭包捕获。迁移后改为显式注入：

- 需要 LLM 的节点函数签名加 `*, llm` 关键字参数：`supervisor(state, *, llm)`
- `build_graph()` 中用 `functools.partial` 预填充：
  ```python
  builder.add_node("supervisor", partial(supervisor, llm=llm))
  ```
- 纯代码节点（dispatcher、plan_review、manual_handler、hitl_handler）不需要 llm，直接注册

Supervisor 的 lazy agent 缓存改为模块级 `_agent_cache: dict[int, Agent]`，以 `id(llm)` 为 key。

### 2. Prompt 外部化

- 从 notebook 中提取 3 个 prompt 字符串 → `Agent/prompts/*.txt`
- 用 `{skills_text}`、`{error_info}` 等占位符，运行时 `.format()` 填充
- 加载工具函数（放在各 node 模块内或共用的 `_load_prompt()` 辅助函数）：
  ```python
  _PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
  def _load_prompt(name): return (_PROMPTS_DIR / name).read_text(encoding="utf-8")
  ```
- **注意 Windows 编码**：必须显式 `encoding="utf-8"`

### 3. RoboDK 初始化 — 调用者负责

- `build_graph()` **不初始化** RoboDK，只组装图拓扑
- 节点内部继续通过 `RobotContext.instance()` / `SkillRegistry.instance()` 获取单例
- 调用者（CLI / GUI / 测试）在 `graph.invoke()` 之前必须先初始化：
  ```python
  from SkiLib.robotcontext import RobotContext
  context = RobotContext()
  context.debug_skip_check = True  # 可选
  ```
- `build_graph()` 入口可加断言：`assert RobotContext.instance() is not None`，给出清晰报错

### 4. GUI 解耦

- `Agent/graph.py` 和所有 `Agent/nodes/` 模块 **零 Gradio 导入**
- `Agent/gui.py` 是叶子消费者，导入 `build_graph` 并驱动 UI
- `log_queue` 由 GUI 创建并通过 `attach_queue_handler()` 挂载，节点只用 `get_logger()`
- 非 GUI 模式下不创建 queue，日志走控制台+文件

### 5. LLM 工厂（`Agent/llm.py`）

```python
def create_llm(provider: str = None, **kwargs) -> BaseChatModel:
    provider = provider or os.getenv("ROBOSKI_LLM_PROVIDER", "claude")
    if provider == "claude":
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        return ChatAnthropic(model=model, **kwargs)
    elif provider == "ollama":
        model_id = os.getenv("OLLAMA_MODEL_ID", "qwen3:latest")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(model=model_id, base_url=base_url, temperature=0, **kwargs)
```

环境变量控制，无需改代码切换 provider。

---

## 实施步骤（按依赖顺序）

### Step 1: `Agent/state.py`
- 提取 `GlobalState` TypedDict 定义
- 无外部依赖（仅 `typing`、`operator`、`SkillResult`、`BaseMessage`）

### Step 2: `Agent/llm.py`
- 提取 LLM 工厂函数
- 读 `.env` 中的 `dotenv`

### Step 3: `Agent/prompts/`
- 创建 `supervisor.txt`、`planner.txt`、`executor.txt`
- 从 notebook 中拷贝字符串，替换动态部分为 `{placeholder}`

### Step 4: 纯代码节点（无 LLM 依赖，可并行提取）
- `nodes/dispatcher.py` — `dispatcher()` + `task_router()`
- `nodes/plan_review.py` — `plan_review()` + `plan_review_router()`
- `nodes/manual_handler.py` — `manual_intervention_handler()` + `manual_intervention_router()`
- `nodes/hitl_handler.py` — `hitl_handler()` + `hitl_router()`

### Step 5: LLM 依赖节点
- `nodes/supervisor.py` — `SupervisorOutput` + `supervisor()` + prompt 加载
- `nodes/planner.py` — Task models + `_make_planner_tools()` + `planner()`
- `nodes/executor.py` — `_EscalateHITLException` + `escalate_tool` + `executor()`

### Step 6: `Agent/graph.py`
- `build_graph(llm=None, checkpointer=None) → CompiledStateGraph`
- `make_initial_state(prompt: str) → dict` 辅助函数
- 用 `partial` 注入 llm，组装全部节点和边

### Step 7: `Agent/__init__.py`
- 更新 re-export

### Step 8: `Agent/gui.py`
- 提取 Gradio UI 代码
- `launch_gui(graph, log_queue=None)` 入口

### Step 9: `Agent/__main__.py`
- CLI 入口，支持 `python -m Agent "把零件放到目标位置"`

### Step 10: 更新 `langgraph.json`
- `"robot_assembly": "./Agent/graph.py:build_graph"`

---

## 需关注的风险

| 风险 | 缓解措施 |
|------|----------|
| `functools.partial` 与 LangGraph 签名检查冲突 | 若 LangGraph 检查签名，改用 `lambda state: node(state, llm=llm)` |
| prompt 文件 Windows 编码问题 | 所有 `open()`/`read_text()` 显式 `encoding="utf-8"` |
| `create_agent` API 版本不兼容 | 确认 langchain 版本；不兼容时退回 `llm.bind_tools()` + 手动循环 |
| RoboDK 未初始化就调用 graph | `build_graph()` 加 assert 断言 |

---

## 验证方案

1. **单元测试**：`RobotContext` mock 下逐个测试纯代码节点（dispatcher、plan_review 等）
2. **集成测试**：`debug_skip_check=True` 下 `python -m Agent "把 Part_A_1 放到 Place Part A"`，验证全图流转
3. **GUI 测试**：`python -m Agent.gui` 启动 Gradio，验证 interrupt 交互
4. **LangGraph Studio**：更新 `langgraph.json` 后验证 Studio 能发现并可视化图

---

## 逐步 Checklist

> 每完成一项打 `[x]`，附注日期和备注。

### Phase A — 基础设施（无节点逻辑）

- [ ] **A.1** 创建 `Agent/nodes/` 目录和 `Agent/nodes/__init__.py`
- [ ] **A.2** 创建 `Agent/prompts/` 目录
- [ ] **A.3** 编写 `Agent/state.py`：提取 `GlobalState` TypedDict，确认 import 正确
- [ ] **A.4** 编写 `Agent/llm.py`：`create_llm()` 工厂函数，环境变量读取
- [ ] **A.5** 创建 `Agent/prompts/supervisor.txt`：从 notebook `_build_supervisor_prompt()` 提取，动态部分替换为 `{skills_text}`
- [ ] **A.6** 创建 `Agent/prompts/planner.txt`：从 notebook `_PLANNER_SYSTEM_PROMPT` 提取
- [ ] **A.7** 创建 `Agent/prompts/executor.txt`：从 notebook `_build_executor_prompt()` 提取，动态部分替换为 `{error_info}`

### Phase B — 纯代码节点（无 LLM 依赖）

- [ ] **B.1** 编写 `Agent/nodes/dispatcher.py`：`dispatcher()` + `task_router()`，仅依赖 `state.GlobalState`
- [ ] **B.2** 编写 `Agent/nodes/plan_review.py`：`plan_review()` + `plan_review_router()`，含 `interrupt()` 调用
- [ ] **B.3** 编写 `Agent/nodes/manual_handler.py`：`manual_intervention_handler()` + `manual_intervention_router()`
- [ ] **B.4** 编写 `Agent/nodes/hitl_handler.py`：`hitl_handler()` + `hitl_router()`
- [ ] **B.5** 验证：各纯代码节点可独立 import 无报错

### Phase C — LLM 依赖节点

- [ ] **C.1** 编写 `Agent/nodes/supervisor.py`：`SupervisorOutput` model + `supervisor(state, *, llm)` + prompt 加载 + agent 缓存
- [ ] **C.2** 编写 `Agent/nodes/planner.py`：Task models + `_make_planner_tools()` + `planner(state, *, llm)` + prompt 加载
- [ ] **C.3** 编写 `Agent/nodes/executor.py`：`_EscalateHITLException` + `escalate_tool` + `executor(state, *, llm)` + `post_task_router()` + prompt 加载
- [ ] **C.4** 验证：各 LLM 节点可独立 import 无报错（不需要实际 LLM 连接）

### Phase D — 图组装与入口

- [ ] **D.1** 编写 `Agent/graph.py`：`build_graph(llm, checkpointer)` + `make_initial_state(prompt)`，用 `partial` 注入 llm，组装全部节点和边
- [ ] **D.2** 更新 `Agent/__init__.py`：re-export `build_graph`, `GlobalState`
- [ ] **D.3** 编写 `Agent/__main__.py`：CLI 入口，`python -m Agent "指令"`
- [ ] **D.4** 验证：`from Agent.graph import build_graph` 可执行，`build_graph()` 返回 `CompiledStateGraph`（需 RoboDK 环境）

### Phase E — GUI 与集成

- [ ] **E.1** 编写 `Agent/gui.py`：提取 Gradio UI，`launch_gui(graph, log_queue=None)` 入口
- [ ] **E.2** 更新 `langgraph.json`：指向 `./Agent/graph.py:build_graph`
- [ ] **E.3** 集成测试：`debug_skip_check=True` 下 CLI 全图流转
- [ ] **E.4** GUI 测试：`python -m Agent.gui` 启动 Gradio，验证 interrupt 交互正常
- [ ] **E.5** LangGraph Studio 验证：确认 Studio 能发现并可视化图

### Phase F — 收尾

- [ ] **F.1** 更新 `CLAUDE.md` 目录结构描述
- [ ] **F.2** 更新 `IMPLEMENTATION_CHECKLIST.md` 对应条目
- [ ] **F.3** 确认 notebook `graph_test.ipynb` 仍可运行（或改为 import Agent 包）
- [ ] **F.4** 清理 `SkiLib/graph.py` 错位文件（如已被 `Agent/graph.py` 取代）
