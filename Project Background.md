# 项目背景：大模型驱动的工业级机器人装配系统

> 本文档供 Claude Code 理解项目背景、架构决策与开发约定。
> 开始任何任务前请先阅读本文档。

---

## 1. 项目目标与核心痛点

本项目解决具身智能（Embodied AI）在工业装配场景落地的三大痛点：

| 痛点 | 描述 | 本项目的解法 |
|------|------|------------|
| 语义鸿沟 | 自然语言指令到物理执行动作之间缺乏可靠转译 | 多层 Agent 分工 + 结构化中间表示 |
| 上下文溢出 | 长序列任务导致 LLM 遗忘或混淆早期信息 | Context Flush 节点 + RemoveMessage 精准清理 |
| 物理幻觉 | LLM 在能力边界处幻觉出不存在的动作或工具 | HILP 强制挂起 + `@require_robot_active` 全局锁 |

**输入**：人类粗略的自然语言装配指令（如"将零件 B 放在 A 上并拧紧"）  
**输出**：机器人完成物理装配，或在能力边界处挂起等待人工介入

---

## 2. 技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| Agent 编排 | **LangGraph** (`StateGraph`) | 多 Agent 状态机、条件路由、HILP 节点挂起 |
| LLM 基础设施 | **LangChain Core** | Tool 抽象、消息结构、`RemoveMessage` |
| 大模型 |**Claude** 强能力验证模型 + **本地 LLM**（如 ChatOllama / Llama-3.1） | 车间局域网部署，数据隐私 + 低延迟 |
| 机器人仿真 | **RoboDK** | 运动学求解、碰撞检测、真机前仿真验证 |
| 工艺知识库 | **YAML 规范文件** | 装配工艺、工具清单、人机任务边界定义 |

---

## 3. 架构总览：Plan-and-Execute 多智能体状态机

系统采用"先计划，后分发，再执行"的架构，分为四个严格隔离的层次：

```
自然语言指令
      │
      ▼
┌─────────────────────────────────────────┐
│  LAYER 1 · 调研与规划层                   │
│                                         │
│  Supervisor ──────────→ Planner         │
│  (局部 ReAct 循环)      (结构化输出)       │
│  调用 Task-skills       生成 todo_list   │
│  消除业务未知信息         JSON，抹除调研   │
│                         对话记录         │
└─────────────────────────────────────────┘
                    │
                    ▼
         ┌─────────────────┐
         │  Global State   │  ← LangGraph TypedDict
         │  todo_list      │
         │  current_task   │
         │  robot_state    │
         │  halt_flag      │
         └─────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  LAYER 2 · 执行与清理层                   │
│                                         │
│  Dispatcher ──────────→ Executor        │
│  (纯代码节点)            (局部 ReAct 循环) │
│  pop(0) 提取             动态加载技能     │
│  current_task            物理执行单步     │
│       ↑                       │         │
│       │       Context Flush ←─┘         │
│       │       (纯代码节点)               │
│       └───────RemoveMessage 清理         │
│               状态上报到 Global State     │
└─────────────────────────────────────────┘
          ↕ HALT / Resume
    ┌──────────────────┐
    │  Human Operator  │  ← HILP 强制挂起节点
    │  能力边界外操作   │
    │  审批 / 继续      │
    └──────────────────┘
```

---

## 4. 各 Agent 职责与硬性边界

### 4.1 Supervisor（LLM 节点）
- **职责**：在局部 ReAct 循环中调用 Task-skills，查阅 YAML 工艺规范和 RoboDK 树结构，消除所有业务未知信息直到"知识饱和"
- **硬性边界**：**禁止**计算坐标 `(x, y, z)`，**禁止**调用底层硬件 API。世界里只有 ID 和符号（如 `Target_A`）

### 4.2 Planner（LLM 节点）
- **职责**：使用强制结构化输出（Structured Output）生成严格的 JSON 格式任务队列 `todo_list`，随后抹除调研对话，防止污染下层
- **硬性边界**：输出必须是合法 JSON，不得包含模糊描述

### 4.3 Dispatcher（纯代码节点，非 LLM）
- **职责**：从 `todo_list` 中 `pop(0)` 提取 `current_task` 下发给 Executor
- **硬性边界**：100% 确定性，不得引入任何 LLM 推理。任务流转必须由代码掌控

### 4.4 Executor（LLM 节点）
- **职责**：只关注当前 `current_task`，动态加载所需的底层技能库（P/R/E-skills）完成单步物理动作
- **硬性边界**：**禁止**解析 YAML 规范，**禁止**思考业务逻辑（"为什么要抓这个零件"）

### 4.5 Context Flush（纯代码节点）
- **职责**：单步动作完成后，用 `RemoveMessage` 抹除 Executor 产生的 `ToolMessage` 噪音和报错记录，向上层汇报极简状态
- **关键约束**：清理时机必须精准——删除底层噪音，但**绝不能**删除上层 Supervisor 派发的 `current_task` 目标

---

## 5. 技能库分层（Skill Taxonomy）

```
Metaskills  ── 调度与安全
  └── request_human_intervention
  └── Halt / Resume

Task-skills ── 知识检索（供 Supervisor 使用）
  └── read_yaml_specs
  └── query_robodk_tree

P-skills    ── 感知（供 Executor 使用）
  └── get_robot_pose
  └── detect_target_position

R-skills    ── 动作（供 Executor 使用）
  └── move_to_target
  └── grip / release

E-skills    ── 异常处理（供 Executor 使用）
  └── retry_ik_solution
  └── recover_from_collision
```

所有技能均基于 OOP 基类实现，**禁止**直接将 Python `Exception` / Traceback 抛给 LLM。

---

## 6. 核心安全机制

### 6.1 SkillResult 标准化中间件

底层错误必须经过 `SkillResult` 封装，翻译为 LLM 可读的具身反馈：

```python
@dataclass
class SkillResult:
    success: bool
    execution_phase: ExecutionPhase   # PLANNING / MOVING / GRIPPING / ...
    robot_state: RobotState           # 当前位姿、关节角、夹爪状态
    error_type: Optional[str]         # IK_FAILURE / COLLISION / TIMEOUT / ...
    suggestion: Optional[str]         # "尝试从上方接近" / "请求人工介入" / ...
    data: Optional[dict]              # 技能返回的有效数据
```

**禁止**直接返回 Python traceback（含文件路径、代码行号）给 LLM。

### 6.2 `@require_robot_active` 装饰器

所有底层动作技能必须使用此装饰器。当系统进入 `halt_flag=True` 状态时，从底层锁死一切动作，防止 LLM 幻觉导致撞机。

```python
@require_robot_active
def move_to_target(self, target_id: str) -> SkillResult:
    ...
```

**白名单机制**：`Resume` 和 `request_human_intervention` 技能必须设置 `bypass_halt=True`，否则会造成死锁（系统无法解除 Halt 状态）。

---

## 7. 已知风险与开发注意事项

### 7.1 本地 LLM 能力瓶颈
本地模型（Llama-3.1 等）的 Structured Output 和 multi-tool calling 可靠性弱于 GPT-4/Claude。Planner 的 JSON 输出和 Supervisor 的工具调用是最脆弱的环节，需要：
- 严格的 JSON Schema 约束 + 输出验证
- 失败时的 retry 策略与 fallback 提示

### 7.2 Context Flush 时机
`RemoveMessage` 操作在 LangGraph StateGraph 层执行。Executor 局部 ReAct 循环出错重试时，必须明确界定哪些消息属于"底层噪音"，哪些属于"上层任务目标"，避免误删。

### 7.3 YAML 规范质量
Supervisor 的推理质量完全依赖 YAML 工艺规范的准确性。规范描述模糊时，应触发 `request_human_intervention` 而非让 LLM 自行猜测。

### 7.4 安全死锁防范
引入 Halt 状态后，必须确保：
- `Resume` 技能带有 `bypass_halt=True`
- `request_human_intervention` 带有 `bypass_halt=True`
- 在基类 `__init_subclass__` 或装饰器层统一检查白名单，而非分散在各子类

---

## 8. 目录架构

```
RoboSkiAgent/
├── CLAUDE.md
├── Agent/                          # 实验用 Notebook，不含生产代码
│   ├── langchain_rag.ipynb
│   └── LangGraph.ipynb
└── SkiLib/                         # 核心库
    ├── ARCHITECTURE.md
    ├── __init__.py
    ├── base.py                     # 核心抽象：BasePrimitive / BaseSkill / SkillResult
    ├── robotcontext.py             # 运行时单例：RobotContext / PrimitiveRegistry
    ├── main.py
    ├── RDK_Test.py
    ├── utils.py
    ├── doc/
    │   ├── DEV_NOTES_SkillRegistry.md
    │   ├── IK_SOLVER_USAGE.md
    │   └── IMPLEMENTATION_PLAN_SkillRegistry.md
    ├── examples/
    ├── primitives/
    │   └── motion.py               # MoveJ (完整) / MoveL (execute 完整, check 待实现)
    └── skills/
        └── pick_and_place.py
```
---

## 9. 开发原则（Golden Rules）

1. **大脑不碰坐标**：Supervisor/Planner 只处理符号和 ID，坐标计算全部在底层技能内完成
2. **手脚不看业务**：Executor 只接受参数指令，不解析 YAML，不思考"为什么"
3. **错误必须具身化**：任何底层物理错误必须经 `SkillResult` 翻译，禁止裸露 traceback
4. **流转必须确定性**：任务调度由 Dispatcher（纯代码）掌控，LLM 只负责推理，不负责决定"下一步去哪"
5. **安全优先于完成**：遇到能力边界宁可挂起交人工，不得幻觉出不存在的工具或动作