# RoboSkiAgent 架构图与控制流图

---

## 1. 系统整体分层架构

```mermaid
graph TB
    subgraph INPUT["Input Layer"]
        NL["Natural language instruction<br/>e.g. 'Assemble Part_A into Tray_1'"]
    end

    subgraph LAYER1["Layer 1 · Planning Layer (LLM-driven)"]
        direction LR
        SUP["🧠 Supervisor<br/>────────────────<br/>ReAct loop<br/>Use Task-Skills to resolve ambiguity<br/>Only handles symbols/IDs<br/>❌ Do not compute coordinates"]
        PLAN["📋 Planner<br/>────────────────<br/>Structured output<br/>Generate todo_list JSON<br/>Validation + Retry (×3)<br/>❌ No vague descriptions"]
        SUP -->|"Knowledge saturation / HILP"| PLAN
    end

    subgraph LAYER2["Layer 2 · Execution Layer (Deterministic + LLM)"]
        direction LR
        DISP["⚙️ Dispatcher<br/>────────────────<br/>Code only, no LLM<br/>Only when current_task={}<br/>pop(0) into execution slot"]
        EXEC["🤖 Executor<br/>────────────────<br/>Dynamically loads Skill<br/>Calls skill.execute()<br/>Writes last_result"]
        FLUSH["🧹 Context Flush<br/>────────────────<br/>Code only, no LLM<br/>Success → clear slot<br/>Failure → set halt_flag"]
        DISP --> EXEC --> FLUSH
    end

    subgraph HILP["Human Intervention Layer (HILP)"]
        HI["👤 Human Intervention<br/>────────────────<br/>interrupt() pauses the graph<br/>Wait for retry / abort<br/>Command(resume=...)"]
    end

    subgraph SKILIB["SkiLib Skill Library"]
        direction TB
        SKILLS["Skills (high-level, platform-agnostic)<br/>PickAndPlace / TaskSkills"]
        PRIMS["Primitives (low-level, RoboDK-related)<br/>MoveJ / MoveL / Grasp / Release / SceneQuery"]
        REG["SkillRegistry (singleton)<br/>@skill decorator auto-registers<br/>Dynamically generates LLM Tool Schema"]
        CTX["RobotContext (singleton)<br/>RoboDK connection management<br/>get_current_state()"]
        SKILLS --> PRIMS
        REG --> SKILLS
        CTX --> PRIMS
    end

    NL --> SUP
    PLAN -->|"todo_list"| DISP
    FLUSH -->|"halt_flag=True"| HI
    HI -->|"retry → keep current_task"| EXEC
    HI -->|"abort → clear queue"| FLUSH
    EXEC <-->|"skill.execute()"| SKILIB
    SUP <-->|"task-skills query"| SKILIB
```

---

## 2. Agent 控制流状态机

> [2026-03-13 更新] 新增 manual 任务路径（Dispatcher → HumanIntervention）、needs_hilp 检查、`complete` 动作

```mermaid
stateDiagram-v2
    [*] --> Supervisor : Receive natural language instruction

    state Supervisor {
        [*] --> ReActLoop
        ReActLoop --> ReActLoop : tool_call (query scene/spec)
        ReActLoop --> [*] : no tool_call (knowledge saturation)
        ReActLoop --> HILP_Trigger : call request_human_intervention
    }

    Supervisor --> Planner : knowledge saturation
    HILP_Trigger --> HumanIntervention : interrupt

    state Planner {
        [*] --> StructuredOutput
        StructuredOutput --> Retry : Pydantic validation failed
        Retry --> StructuredOutput : retry (up to 3 times)
        StructuredOutput --> [*] : validation passed (supports mixed auto/manual)
        Retry --> HaltPlanning : retries exhausted
    }
    HaltPlanning --> HumanIntervention

    Planner --> FlushSupervisorMsg : clear Supervisor messages
    FlushSupervisorMsg --> Dispatcher

    state ExecutionLoop {
        state Dispatcher {
            [*] --> CheckSlot
            CheckSlot --> PopTask : current_task == {}
            CheckSlot --> SkipPop : current_task != {}
            PopTask --> CheckType
            CheckType --> AutoPath : type != manual
            CheckType --> ManualPath : type == manual\nhalt_flag=T, halt_reason=MANUAL_TASK
            SkipPop --> [*]
            AutoPath --> [*]
            ManualPath --> [*]
        }

        Dispatcher --> Executor : after_dispatcher=auto
        Dispatcher --> HumanIntervention : after_dispatcher=manual

        state Executor {
            [*] --> ReActLoop2
            ReActLoop2 --> ReActLoop2 : internal recovery retries
            ReActLoop2 --> WriteSuccess : success
            ReActLoop2 --> WriteFailHILP : give up needs_hilp=True
            WriteSuccess --> [*]
            WriteFailHILP --> [*]
        }

        Executor --> ContextFlush

        state ContextFlush {
            [*] --> CheckResult
            CheckResult --> ClearSlot : success=true
            CheckResult --> CheckNeedsHilp : success=false
            CheckNeedsHilp --> SetHalt : needs_hilp=true\nhalt_reason=TASK_FAILURE
            CheckNeedsHilp --> SetHalt : needs_hilp=false\n(conservative fallthrough)
            ClearSlot --> [*]
            SetHalt --> [*]
        }
    }

    ContextFlush --> Dispatcher : todo_list not empty and halt=false
    ContextFlush --> Done : todo_list empty and halt=false
    ContextFlush --> HumanIntervention : halt_flag=true

    state HumanIntervention {
        [*] --> WaitOperator
        WaitOperator --> RetryAction : action=retry\n(valid only for TASK_FAILURE)
        WaitOperator --> CompleteAction : action=complete\n(valid only for MANUAL_TASK)
        WaitOperator --> AbortAction : action=abort
        RetryAction --> [*] : clear halt, keep current_task
        CompleteAction --> [*] : clear halt, clear current_task
        AbortAction --> [*] : clear current_task + todo_list
    }

    HumanIntervention --> Executor : retry → retry same task
    HumanIntervention --> Dispatcher : complete → continue queue
    HumanIntervention --> Done : abort
    Done --> [*]
```

---

## 3. GlobalState 数据流

> [2026-03-13 更新] 新增 `halt_reason`、`_hi_action`；完善各节点写入路径

```mermaid
flowchart LR
    subgraph STATE["GlobalState (LangGraph shared state)"]
        direction TB
        MSG["messages: list[BaseMessage]<br/>message bus (operator.add)"]
        TODO["todo_list: list[dict]<br/>supports mixed auto/manual"]
        CUR["current_task: dict<br/>execution slot: {} = idle"]
        LAST["last_result: Optional[dict]<br/>includes needs_hilp field"]
        HALT["halt_flag: bool<br/>HILP trigger"]
        HREASON["halt_reason: Optional[str]<br/>TASK_FAILURE / MANUAL_TASK"]
        LOG["execution_log: list[str]<br/>audit trail (operator.add)"]
        RS["robot_state: RobotState<br/>pose/joint snapshot"]
    end

    PLAN_NODE["Planner"] -->|"write"| TODO
    DISP_NODE["Dispatcher"] -->|"pop(0) fill"| CUR
    DISP_NODE -->|"update"| TODO
    DISP_NODE -->|"manual task: set True"| HALT
    DISP_NODE -->|"manual task: write"| HREASON
    EXEC_NODE["Executor"] -->|"write (includes needs_hilp)"| LAST
    EXEC_NODE -->|"write"| LOG
    CF_NODE["Context Flush"] -->|"success: clear"| CUR
    CF_NODE -->|"success: clear"| LAST
    CF_NODE -->|"success: clear"| HREASON
    CF_NODE -->|"failure needs_hilp=T: set True"| HALT
    CF_NODE -->|"failure: write TASK_FAILURE"| HREASON
    CF_NODE -->|"write"| LOG
    HI_NODE["Human Intervention"] -->|"all actions: clear"| HALT
    HI_NODE -->|"all actions: clear"| HREASON
    HI_NODE -->|"complete/abort: clear"| CUR
    HI_NODE -->|"abort: clear"| TODO
```

---

## 4. SkiLib 组件架构

```mermaid
graph TB
    subgraph AGENT["Agent Layer (LangGraph)"]
        SUP2["Supervisor"]
        EXEC2["Executor"]
    end

    subgraph REGISTRY["SkillRegistry (singleton)"]
        DEC["@skill decorator<br/>auto-register on import"]
        SCHEMA["get_llm_tool_schemas()<br/>Anthropic format"]
        LIST["list_skills(category)<br/>queried by Supervisor"]
    end

    subgraph SKILLS2["Skills (platform-agnostic)"]
        PAP["PickAndPlace"]
        TS["TaskSkills<br/>list_targets / get_target_info<br/>query_assembly_spec<br/>request_human_intervention"]
    end

    subgraph PRIMS2["Primitives (RoboDK-related)"]
        MJ["MoveJ<br/>joint motion"]
        ML["MoveL<br/>linear motion"]
        GR["Grasp / Release<br/>gripper"]
        SQ["SceneQuery<br/>ListItems / GetTargetPose<br/>GetApproachTarget / CheckReachable"]
    end

    subgraph CONTEXT["RobotContext (singleton)"]
        RDK2["RoboDK connection<br/>RDK + robot object"]
        STATE2["get_current_state()<br/>→ RobotState snapshot"]
    end

    subgraph SPECS["Process specs"]
        YAML["specs/*.yaml<br/>Part IDs / target positions / process constraints"]
    end

    SUP2 -->|"tool call"| SCHEMA
    EXEC2 -->|"dynamic load"| REGISTRY
    REGISTRY --> SKILLS2
    SKILLS2 --> PRIMS2
    PRIMS2 --> CONTEXT
    TS --> YAML
    REGISTRY -->|"set_robot_context()"| CONTEXT

    style AGENT fill:#dbeafe,stroke:#3b82f6
    style REGISTRY fill:#fef3c7,stroke:#f59e0b
    style SKILLS2 fill:#d1fae5,stroke:#10b981
    style PRIMS2 fill:#fce7f3,stroke:#ec4899
    style CONTEXT fill:#ede9fe,stroke:#8b5cf6
    style SPECS fill:#f3f4f6,stroke:#9ca3af
```

---

## 5. 执行槽（current_task）生命周期

> [2026-03-13 更新] 新增 ManualPending 状态和 complete 转换

```mermaid
stateDiagram-v2
    [*] --> Empty : initialized

    Empty : current_task = {}
    AutoOccupied : current_task = {type=auto, skill, params}
    ManualPending : current_task = {type=manual, description}\nhalt_flag=T, halt_reason=MANUAL_TASK
    Failed : current_task retained (unchanged)\nhalt_flag=T, halt_reason=TASK_FAILURE

    Empty --> AutoOccupied : Dispatcher.pop(0) type=auto
    Empty --> ManualPending : Dispatcher.pop(0) type=manual
    AutoOccupied --> Empty : Context Flush (success=True)
    AutoOccupied --> Failed : Context Flush (needs_hilp=True)
    Failed --> AutoOccupied : Human Intervention action=retry\n(clear halt, keep slot)
    Failed --> Empty : Human Intervention action=abort
    ManualPending --> Empty : Human Intervention action=complete\n(clear halt, clear slot)
    ManualPending --> Empty : Human Intervention action=abort

    note right of Empty : Dispatcher fills a new task only when slot is {}
    note right of Failed : Dispatcher skips non-empty slot and does not overwrite
    note right of ManualPending : Executor is bypassed; wait directly for operator
```

---

## 6. Agent 高层控制流（概览）

> 展示从自然语言指令到任务完成的完整主路径，以及 HILP 挂起与恢复的关键分支。
> 省略节点内部细节（ReAct 循环、retry 逻辑等），聚焦于**节点间的路由决策**。

```mermaid
flowchart TD
    START(["🟢 Natural language instruction<br/>e.g. 'Place Part_A into Tray_1'"])

    subgraph L1["Layer 1 · Planning"]
        SUP["🧠 Supervisor<br/><i>ReAct loop · knowledge saturation</i>"]
        PLAN["📋 Planner<br/><i>structured output · generate todo_list</i>"]
        SUP -->|"knowledge saturated"| PLAN
    end

    subgraph L2["Layer 2 · Execution loop"]
        DISP["⚙️ Dispatcher<br/><i>pop(0) when slot is empty</i>"]
        EXEC["🤖 Executor<br/><i>load Skill · execute · write last_result</i>"]
        CF["🧹 Context Flush<br/><i>pure code · inspect last_result</i>"]

        DISP -->|"type=auto"| EXEC
        EXEC --> CF
    end

    subgraph HILP["Human Intervention Layer"]
        HI["👤 Human Intervention<br/><i>interrupt() · await operator action</i>"]
    end

    DONE(["🏁 Done"])

    %% ── happy path ──
    START --> SUP
    PLAN -->|"todo_list"| DISP

    %% ── Dispatcher branches ──
    DISP -->|"type=manual<br/>halt_reason=MANUAL_TASK"| HI
    DISP -->|"queue empty · slot empty"| DONE

    %% ── Context Flush branches ──
    CF -->|"✅ success<br/>clear slot"| DISP
    CF -->|"📭 queue empty · no halt"| DONE
    CF -->|"❌ needs_hilp=True<br/>halt_reason=TASK_FAILURE"| HI

    %% ── HumanIntervention exits ──
    HI -->|"retry<br/>keep current_task"| EXEC
    HI -->|"complete<br/>clear current_task"| DISP
    HI -->|"abort<br/>clear queue"| DONE

    %% ── Supervisor HILP ──
    SUP -.->|"request_human_intervention"| HI

    %% ── 样式 ──
    style L1   fill:#dbeafe,stroke:#3b82f6
    style L2   fill:#d1fae5,stroke:#10b981
    style HILP fill:#fef3c7,stroke:#f59e0b
    style START fill:#f0fdf4,stroke:#16a34a
    style DONE  fill:#f0fdf4,stroke:#16a34a
    style HI    fill:#fef9c3,stroke:#ca8a04
```

---

## 7. @require_robot_active 守卫机制

```mermaid
flowchart TD
    CALL["R-skill call<br/>e.g. move_to_target()"]
    CHECK{{"halt_flag == True ?"}}
    BYPASS{{"bypass_halt == True ?"}}
    BLOCK["Return SkillResult\nsuccess=False\nerror_type=ROBOT_INACTIVE"]
    EXEC3["Execute skill normally"]

    CALL --> CHECK
    CHECK -->|"No"| EXEC3
    CHECK -->|"Yes"| BYPASS
    BYPASS -->|"No (regular R-skill)"| BLOCK
    BYPASS -->|"Yes (whitelist)"| EXEC3

    subgraph WHITELIST["Whitelist (bypass_halt=True)"]
        R["resume()"]
        RHI["request_human_intervention()"]
    end

    EXEC3 -.-> WHITELIST
```
