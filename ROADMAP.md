# RoboSkiAgent — Strengths, Limitations & Roadmap

> For presentation use. Last updated: 2026-03-30.

---

## Strengths

```mermaid
mindmap
  root((RoboSkiAgent))
    Architecture
      Agent ↔ SkiLib fully decoupled
        SkiLib has zero LangGraph dependency
        Can be tested standalone
      Deterministic execution control
        Dispatcher is pure code — no LLM in routing
        Task flow is 100% predictable and auditable
      Symbol-only planning
        Supervisor / Planner never touch coordinates
        Eliminates an entire class of spatial hallucinations
    Safety
      Structural HITL
        interrupt() enforced by graph topology
        Even a weak LLM cannot bypass the approval gate
      @require_robot_active global lock
        halt_flag=True freezes all R-skills at the base layer
        Prevents physical damage from hallucinated actions
      Error embodiment via SkillResult
        All hardware errors translated to structured LLM-readable feedback
        Raw tracebacks never reach the LLM
    Extensibility
      Reflection-based skill discovery
        Drop a new .py in skills/ — auto-registered, no config
      Auto tool schema generation
        New skill immediately appears in Planner's tool set
        Schema derived from try_execute signature via Pydantic
      LLM-agnostic factory
        Switch Claude ↔ Ollama via env var ROBOSKI_LLM_PROVIDER
        No code changes required
```

---

## Current Limitations

| # | Limitation | Impact | Root Cause |
|---|-----------|--------|------------|
| **L1** | **Only 1 skill (PickAndPlace)** — cannot validate multi-skill planning or skill selection | Cannot stress-test Planner's composition ability; LLM has no meaningful choice to make | Phase 1 skills not yet built |
| **L2** | **No Perception layer** — target positions must be pre-defined static RoboDK targets | Breaks in real assembly where part poses vary at runtime | No camera / sensor primitive |
| **L3** | **Phase 1 (scene_query + YAML spec) unbuilt** — Supervisor cannot read process specs | Assembly knowledge must be in the prompt; no structured process knowledge base | Phase 1 not started |
| **L4** | **RemoveMessage not implemented** — Executor / Planner messages accumulate in `messages` | Context grows unboundedly in long sequences; LLM performance degrades | Checklist item 3.5.7 / 4.3 |
| **L5** | **CLI interrupt handling missing** — `graph.invoke()` raises `NodeInterrupt` at any HITL gate | CLI cannot run end-to-end flows requiring human approval | Checklist 6.5 |
| **L6** | **`SkiLib/graph.py` misplaced** — contains LangGraph import, violates SkiLib's no-LangGraph rule | Architectural boundary leak; risks unintended coupling | Historical artifact, never cleaned |
| **L7** | **Only simulation tested** — Grasp/Release real-robot paths are `# TODO setDO + feedback wait` | Unknown whether skill timing / error handling holds on real hardware | Real robot integration not started |

---

## Future Extension Points

How the system grows — organized by architectural layer.

```mermaid
flowchart TB
    subgraph NOW["Current State"]
        direction LR
        SUP_N["Supervisor\nT-skills: list_targets\nlist_objects / tools"]
        PLAN_N["Planner\n1 skill: PickAndPlace"]
        EXE_N["Executor\ntry_execute + LLM recovery"]
        PaP_N["PickAndPlace\n8-step sequence"]
        PRIMS_N["MoveJ · MoveL\nGrasp · Release"]
    end

    subgraph FUTURE["Future Extensions  (by layer)"]
        direction TB

        subgraph KL["🧠 Knowledge Layer  (new)"]
            RAG["RAG Knowledge Base\nVector DB + embedded PDF/CAD specs\nSupervisor queries instead of static YAML"]
            LEARN["HITL Learning Loop\nRecord operator replan corrections\nas few-shot examples → improve Planner"]
        end

        subgraph SL["📋 Skills Layer  (new skills)"]
            DOC["DocReader Skill\nParse technical PDF / CAD spec\nExtract assembly sequence → Planner context"]
            INSP["Inspection Skill\nCapture image → vision model → pass/fail"]
            FAST["Fastening Skill\nScrew-drive sequence + torque verification"]
            ASSY["AssemblyVerify Skill\nPost-step geometric check via camera"]
        end

        subgraph PL["⚙️ Primitives Layer  (new primitives)"]
            CAM["CameraCapture\nPickit 3D / depth camera\nReturns real-time target pose → replaces static RoboDK target"]
            SCREWP["ScrewDrive\nRotational motion + force feedback exit condition"]
            FORCE["ForceProbe\nCompliance move until contact force threshold"]
            CONV["ConveyorTrack\nOnline target following during MoveL"]
        end

        subgraph SYS["🔧 System Level"]
            MULTI["Multi-robot Coordination\nPer-robot GlobalState instances\nShared todo_list with resource locking"]
            STREAM["Streaming Execution\nParallel independent tasks\nDAG dependency resolution in Dispatcher"]
            EVAL["LLM Planning Benchmark\nAutomatic ground-truth comparison\nMeasure skill selection accuracy"]
        end
    end

    NOW -.->|"grows into"| FUTURE

    style KL   fill:#ede9fe,stroke:#8b5cf6
    style SL   fill:#d1fae5,stroke:#10b981
    style PL   fill:#fce7f3,stroke:#ec4899
    style SYS  fill:#f3f4f6,stroke:#6b7280
    style NOW  fill:#dbeafe,stroke:#3b82f6
```

---

## Priority Roadmap

What to build next, and why.

```mermaid
timeline
    title Development Priorities

    section Unblock Validation
        Phase 1 — scene_query primitives    : Enable Supervisor to check reachability
        Phase 1 — YAML assembly spec        : Give Supervisor structured process knowledge
        2nd Skill — Inspection or Fastening : Enable multi-skill plan composition tests

    section Harden Current System
        RemoveMessage context cleanup       : Prevent context bloat in long sequences
        CLI interrupt handling              : Full end-to-end flow without GUI
        SkiLib/graph.py cleanup             : Remove architectural boundary leak

    section Perception Upgrade
        CameraCapture primitive             : Replace static targets with runtime poses
        Pickit 3D integration               : Real-world part localization

    section Knowledge Expansion
        DocReader skill                     : LLM reads technical manuals directly
        RAG knowledge base                  : Replace static YAML with queryable vector DB

    section Scale
        Multi-robot support                 : Coordinate parallel assembly stations
        HITL learning loop                  : Operator corrections → training data
```

---

## Key Design Tensions

Decisions that involve deliberate trade-offs worth discussing.

| Tension | Current Choice | Alternative | Why this choice |
|---------|---------------|-------------|-----------------|
| **Explicit vs. implicit approach points** | Explicit params (`pick_approach`, `place_approach`) | Auto-lookup by naming convention (`Approach_<target>`) | LLM must reason about all parameters explicitly; hidden conventions are opaque to the model |
| **Structural vs. prompt-based HITL** | `interrupt()` in graph topology | Prompt: "always ask before executing" | Prompt-based gates fail with weaker models; structural gates are model-agnostic |
| **Tool-call planning vs. structured JSON** | Dynamic `add_<Skill>_task` tool calls | `with_structured_output(TodoList)` | Tool calling degrades gracefully; JSON schema output breaks silently when model forgets fields |
| **SkillResult vs. raw exceptions** | All errors wrapped in `SkillResult` | Pass exceptions to LLM | Raw tracebacks leak file paths and line numbers; structured errors provide actionable suggestions |
| **Single execution slot** | `current_task: dict` — one task at a time | Queue-based parallel execution | Simplifies HITL recovery (retry = re-run same slot); parallel execution complicates interrupt semantics |

---

---

# RoboSkiAgent — 优势、不足与发展路线（中文版）

> 演示文稿用。最后更新：2026-03-30。

---

## 优势

```mermaid
mindmap
  root((RoboSkiAgent))
    架构设计
      Agent 与 SkiLib 完全解耦
        SkiLib 零 LangGraph 依赖
        可独立测试和部署
      确定性执行控制
        Dispatcher 是纯代码节点，路由不依赖 LLM
        任务流转 100% 可预测、可审计
      符号层规划
        Supervisor / Planner 永不接触坐标
        从根本上消除一整类空间幻觉问题
    安全机制
      结构性人机协作门（HITL）
        interrupt() 由图拓扑强制触发
        即使是弱模型也无法绕过审批节点
      @require_robot_active 全局锁
        halt_flag=True 时从底层冻结所有动作技能
        防止 LLM 幻觉动作导致物理碰撞
      错误具身化（SkillResult）
        所有硬件错误翻译为结构化 LLM 可读反馈
        原始 traceback 永不传递给 LLM
    可扩展性
      基于反射的技能发现
        新建 .py 放入 skills/ 即自动注册，无需配置
      工具 Schema 自动生成
        新技能立即出现在 Planner 的工具集中
        Schema 由 try_execute 签名经 Pydantic 自动推导
      LLM 无关工厂
        通过环境变量 ROBOSKI_LLM_PROVIDER 切换 Claude / Ollama
        无需修改任何代码
```

---

## 当前不足

| # | 不足 | 影响 | 根因 |
|---|------|------|------|
| **L1** | **只有 1 个 Skill（PickAndPlace）**，无法验证多技能规划与选择能力 | Planner 的组合规划能力无从测试；LLM 实际上没有有意义的技能选择 | Phase 1 技能尚未建立 |
| **L2** | **没有感知层**——目标位置必须是 RoboDK 中预定义的静态目标点 | 真实装配中零件位姿随机变化，系统无法适应 | 没有相机 / 传感器 Primitive |
| **L3** | **Phase 1（场景查询 + YAML 工艺规范）未实现**——Supervisor 无法读取工艺规格 | 装配知识只能写在 prompt 里，没有结构化知识库 | Phase 1 尚未启动 |
| **L4** | **RemoveMessage 未实现**——Executor / Planner 消息在 `messages` 中持续堆积 | 长序列任务 context 无限膨胀，LLM 性能下降 | Checklist 3.5.7 / 4.3 |
| **L5** | **CLI 中断处理缺失**——`graph.invoke()` 在任意 HITL 节点抛出 `NodeInterrupt` | CLI 无法完成含人工审批的完整流程 | Checklist 6.5 |
| **L6** | **`SkiLib/graph.py` 错位**——含 LangGraph 导入，违反 SkiLib 无 LangGraph 约束 | 架构边界渗漏，存在意外耦合风险 | 历史遗留，从未清理 |
| **L7** | **只在仿真中测试**——Grasp/Release 真机路径为 `# TODO setDO + feedback wait` | 不确定真机上的技能时序与错误处理是否有效 | 真机集成尚未启动 |

---

## 未来扩展点

按架构层次组织，展示系统可以往哪里生长。

```mermaid
flowchart TB
    subgraph NOW["当前状态"]
        direction LR
        SUP_N["Supervisor\nT-skills：列举目标/对象/工具"]
        PLAN_N["Planner\n1 个技能：PickAndPlace"]
        EXE_N["Executor\ntry_execute + LLM 恢复循环"]
        PaP_N["PickAndPlace\n8 步执行序列"]
        PRIMS_N["MoveJ · MoveL\nGrasp · Release"]
    end

    subgraph FUTURE["未来扩展（按层次）"]
        direction TB

        subgraph KL["🧠 知识层（新增）"]
            RAG["RAG 知识库\n向量数据库 + PDF/CAD 规格嵌入\nSupervisor 查询替代静态 YAML"]
            LEARN["HITL 学习闭环\n记录操作员 replan 修正\n转化为 few-shot 示例改进 Planner"]
        end

        subgraph SL["📋 技能层（新增技能）"]
            DOC["DocReader 技能\n解析技术文档 PDF / CAD 规格\n提取装配工序 → 注入 Planner 上下文"]
            INSP["Inspection 检测技能\n拍照 → 视觉模型 → 合格/不合格判断"]
            FAST["Fastening 拧紧技能\n螺丝驱动序列 + 扭矩验证"]
            ASSY["AssemblyVerify 验证技能\n单步完成后通过相机做几何校验"]
        end

        subgraph PL["⚙️ 原语层（新增原语）"]
            CAM["CameraCapture\nPickit 3D / 深度相机\n返回实时目标位姿，替代静态 RoboDK 目标点"]
            SCREWP["ScrewDrive\n旋转运动 + 力反馈退出条件"]
            FORCE["ForceProbe\n柔顺运动直到接触力达到阈值"]
            CONV["ConveyorTrack\nMoveL 过程中在线跟踪传送带目标"]
        end

        subgraph SYS["🔧 系统层"]
            MULTI["多机器人协调\n每台机器人独立 GlobalState\n共享 todo_list + 资源锁"]
            STREAM["流式并行执行\n独立任务并行，Dispatcher 做 DAG 依赖解析"]
            EVAL["LLM 规划评测基准\n自动对比 ground truth\n量化技能选择与参数准确率"]
        end
    end

    NOW -.->|"扩展为"| FUTURE

    style KL   fill:#ede9fe,stroke:#8b5cf6
    style SL   fill:#d1fae5,stroke:#10b981
    style PL   fill:#fce7f3,stroke:#ec4899
    style SYS  fill:#f3f4f6,stroke:#6b7280
    style NOW  fill:#dbeafe,stroke:#3b82f6
```

---

## 优先级路线图

```mermaid
timeline
    title 开发优先级

    section 解锁验证能力
        Phase 1 — scene_query 原语      : 让 Supervisor 能检查可达性
        Phase 1 — YAML 工艺规范         : 给 Supervisor 结构化工艺知识
        第 2 个技能 — Inspection 或 Fastening : 支持多技能规划组合测试

    section 加固现有系统
        RemoveMessage 上下文清理        : 防止长序列 context 膨胀
        CLI 中断处理                    : 不依赖 GUI 完成完整流程
        SkiLib/graph.py 清理            : 消除架构边界渗漏

    section 感知层升级
        CameraCapture 原语              : 用运行时位姿替代静态目标点
        Pickit 3D 集成                  : 真实场景零件定位

    section 知识层扩展
        DocReader 技能                  : LLM 直接阅读技术手册
        RAG 知识库                      : 用可查询向量库替代静态 YAML

    section 规模扩展
        多机器人支持                    : 协调多个装配工位并行执行
        HITL 学习闭环                   : 操作员修正转化为训练数据
```

---

## 关键设计取舍

演示 Q&A 环节可能被问到的设计决策。

| 取舍点 | 当前选择 | 另一种方案 | 选择理由 |
|--------|---------|-----------|---------|
| **接近点：显式 vs 隐式** | 显式参数（`pick_approach`、`place_approach`） | 按命名约定自动查找（`Approach_<target>`） | LLM 必须明确推理所有参数；隐式约定对模型不透明，调试困难 |
| **HITL：结构性 vs 提示词** | `interrupt()` 在图拓扑中强制触发 | prompt 要求"执行前必须请示" | 提示词门控在弱模型上失效；结构性门控与模型能力无关 |
| **规划：工具调用 vs 结构化 JSON** | 动态 `add_<Skill>_task` 工具调用 | `with_structured_output(TodoList)` | 工具调用降级优雅；JSON schema 输出在模型遗漏字段时静默失败 |
| **错误：SkillResult vs 原始异常** | 所有错误封装进 `SkillResult` | 原始 Exception / traceback 传给 LLM | 裸 traceback 泄露文件路径和行号；结构化错误提供可操作建议 |
| **执行槽：单任务 vs 并行队列** | `current_task: dict`，单任务执行槽 | 并行任务队列 | 简化 HITL 恢复语义（retry = 重跑同一槽位）；并行执行使 interrupt 语义复杂化 |
