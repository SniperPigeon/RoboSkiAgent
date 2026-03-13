# RoboSkiAgent 架构图与控制流图

---

## 1. 系统整体分层架构

```mermaid
graph TB
    subgraph INPUT["输入层"]
        NL["自然语言指令<br/>e.g. '将 Part_A 装入 Tray_1'"]
    end

    subgraph LAYER1["Layer 1 · 规划层（LLM 驱动）"]
        direction LR
        SUP["🧠 Supervisor<br/>────────────────<br/>ReAct 循环<br/>调用 Task-Skills 消歧义<br/>只处理符号/ID<br/>❌ 禁止计算坐标"]
        PLAN["📋 Planner<br/>────────────────<br/>结构化输出<br/>生成 todo_list JSON<br/>校验 + Retry(×3)<br/>❌ 禁止模糊描述"]
        SUP -->|"知识饱和 / HILP"| PLAN
    end

    subgraph LAYER2["Layer 2 · 执行层（确定性 + LLM）"]
        direction LR
        DISP["⚙️ Dispatcher<br/>────────────────<br/>纯代码，无 LLM<br/>仅在 current_task={}时<br/>pop(0) 填入执行槽"]
        EXEC["🤖 Executor<br/>────────────────<br/>动态加载 Skill<br/>调用 skill.execute()<br/>写入 last_result"]
        FLUSH["🧹 Context Flush<br/>────────────────<br/>纯代码，无 LLM<br/>成功→清空槽<br/>失败→设 halt_flag"]
        DISP --> EXEC --> FLUSH
    end

    subgraph HILP["人工干预层（HILP）"]
        HI["👤 Human Intervention<br/>────────────────<br/>interrupt() 暂停图<br/>等待 retry / abort<br/>Command(resume=...)"]
    end

    subgraph SKILIB["SkiLib 技能库"]
        direction TB
        SKILLS["Skills（高层，平台无关）<br/>PickAndPlace / TaskSkills"]
        PRIMS["Primitives（底层，RoboDK 相关）<br/>MoveJ / MoveL / Grasp / Release / SceneQuery"]
        REG["SkillRegistry（单例）<br/>@skill 装饰器自动注册<br/>动态生成 LLM Tool Schema"]
        CTX["RobotContext（单例）<br/>RoboDK 连接管理<br/>get_current_state()"]
        SKILLS --> PRIMS
        REG --> SKILLS
        CTX --> PRIMS
    end

    NL --> SUP
    PLAN -->|"todo_list"| DISP
    FLUSH -->|"halt_flag=True"| HI
    HI -->|"retry → 保留 current_task"| EXEC
    HI -->|"abort → 清空队列"| FLUSH
    EXEC <-->|"skill.execute()"| SKILIB
    SUP <-->|"task-skills 查询"| SKILIB
```

---

## 2. Agent 控制流状态机

> [2026-03-13 更新] 新增 manual 任务路径（Dispatcher → HumanIntervention）、needs_hilp 检查、`complete` 动作

```mermaid
stateDiagram-v2
    [*] --> Supervisor : 收到自然语言指令

    state Supervisor {
        [*] --> ReActLoop
        ReActLoop --> ReActLoop : tool_call（查询场景/规范）
        ReActLoop --> [*] : 无 tool_call（知识饱和）
        ReActLoop --> HILP_Trigger : 调用 request_human_intervention
    }

    Supervisor --> Planner : 知识饱和
    HILP_Trigger --> HumanIntervention : interrupt

    state Planner {
        [*] --> StructuredOutput
        StructuredOutput --> Retry : Pydantic 校验失败
        Retry --> StructuredOutput : 重试（最多3次）
        StructuredOutput --> [*] : 校验通过（支持 auto/manual 混排）
        Retry --> HaltPlanning : 重试耗尽
    }
    HaltPlanning --> HumanIntervention

    Planner --> FlushSupervisorMsg : 清除 Supervisor 消息
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
            ReActLoop2 --> ReActLoop2 : 内部恢复重试
            ReActLoop2 --> WriteSuccess : 成功
            ReActLoop2 --> WriteFailHILP : 放弃 needs_hilp=True
            WriteSuccess --> [*]
            WriteFailHILP --> [*]
        }

        Executor --> ContextFlush

        state ContextFlush {
            [*] --> CheckResult
            CheckResult --> ClearSlot : success=true
            CheckResult --> CheckNeedsHilp : success=false
            CheckNeedsHilp --> SetHalt : needs_hilp=true\nhalt_reason=TASK_FAILURE
            CheckNeedsHilp --> SetHalt : needs_hilp=false\n（保守 fallthrough）
            ClearSlot --> [*]
            SetHalt --> [*]
        }
    }

    ContextFlush --> Dispatcher : todo_list 非空 且 halt=false
    ContextFlush --> Done : todo_list 空 且 halt=false
    ContextFlush --> HumanIntervention : halt_flag=true

    state HumanIntervention {
        [*] --> WaitOperator
        WaitOperator --> RetryAction : action=retry\n（仅 TASK_FAILURE 有效）
        WaitOperator --> CompleteAction : action=complete\n（仅 MANUAL_TASK 有效）
        WaitOperator --> AbortAction : action=abort
        RetryAction --> [*] : 清除 halt，保留 current_task
        CompleteAction --> [*] : 清除 halt，清空 current_task
        AbortAction --> [*] : 清空 current_task + todo_list
    }

    HumanIntervention --> Executor : retry → 重试同一任务
    HumanIntervention --> Dispatcher : complete → 继续队列
    HumanIntervention --> Done : abort
    Done --> [*]
```

---

## 3. GlobalState 数据流

> [2026-03-13 更新] 新增 `halt_reason`、`_hi_action`；完善各节点写入路径

```mermaid
flowchart LR
    subgraph STATE["GlobalState（LangGraph 共享状态）"]
        direction TB
        MSG["messages: list[BaseMessage]<br/>消息总线（operator.add）"]
        TODO["todo_list: list[dict]<br/>支持 auto/manual 混排"]
        CUR["current_task: dict<br/>执行槽：{} = 空闲"]
        LAST["last_result: Optional[dict]<br/>含 needs_hilp 字段"]
        HALT["halt_flag: bool<br/>HILP 触发器"]
        HREASON["halt_reason: Optional[str]<br/>TASK_FAILURE / MANUAL_TASK"]
        LOG["execution_log: list[str]<br/>审计轨迹（operator.add）"]
        RS["robot_state: RobotState<br/>位姿/关节角快照"]
    end

    PLAN_NODE["Planner"] -->|"写入"| TODO
    DISP_NODE["Dispatcher"] -->|"pop(0) 填入"| CUR
    DISP_NODE -->|"更新"| TODO
    DISP_NODE -->|"manual任务：设True"| HALT
    DISP_NODE -->|"manual任务：写入"| HREASON
    EXEC_NODE["Executor"] -->|"写入（含needs_hilp）"| LAST
    EXEC_NODE -->|"写入"| LOG
    CF_NODE["Context Flush"] -->|"成功：清空"| CUR
    CF_NODE -->|"成功：清空"| LAST
    CF_NODE -->|"成功：清空"| HREASON
    CF_NODE -->|"失败needs_hilp=T：设True"| HALT
    CF_NODE -->|"失败：写入TASK_FAILURE"| HREASON
    CF_NODE -->|"写入"| LOG
    HI_NODE["Human Intervention"] -->|"所有动作：清除"| HALT
    HI_NODE -->|"所有动作：清除"| HREASON
    HI_NODE -->|"complete/abort：清空"| CUR
    HI_NODE -->|"abort：清空"| TODO
```

---

## 4. SkiLib 组件架构

```mermaid
graph TB
    subgraph AGENT["Agent 层（LangGraph）"]
        SUP2["Supervisor"]
        EXEC2["Executor"]
    end

    subgraph REGISTRY["SkillRegistry（单例）"]
        DEC["@skill 装饰器<br/>import 时自动注册"]
        SCHEMA["get_llm_tool_schemas()<br/>Anthropic 格式"]
        LIST["list_skills(category)<br/>供 Supervisor 查询"]
    end

    subgraph SKILLS2["Skills（平台无关）"]
        PAP["PickAndPlace"]
        TS["TaskSkills<br/>list_targets / get_target_info<br/>query_assembly_spec<br/>request_human_intervention"]
    end

    subgraph PRIMS2["Primitives（RoboDK 相关）"]
        MJ["MoveJ<br/>关节运动"]
        ML["MoveL<br/>直线运动"]
        GR["Grasp / Release<br/>夹爪"]
        SQ["SceneQuery<br/>ListItems / GetTargetPose<br/>GetApproachTarget / CheckReachable"]
    end

    subgraph CONTEXT["RobotContext（单例）"]
        RDK2["RoboDK 连接<br/>RDK + robot 对象"]
        STATE2["get_current_state()<br/>→ RobotState 快照"]
    end

    subgraph SPECS["工艺规范"]
        YAML["specs/*.yaml<br/>零件ID / 目标位置 / 工序约束"]
    end

    SUP2 -->|"工具调用"| SCHEMA
    EXEC2 -->|"动态加载"| REGISTRY
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
    [*] --> Empty : 初始化

    Empty : current_task = {}
    AutoOccupied : current_task = {type=auto, skill, params}
    ManualPending : current_task = {type=manual, description}\nhalt_flag=T, halt_reason=MANUAL_TASK
    Failed : current_task 保留（原值不变）\nhalt_flag=T, halt_reason=TASK_FAILURE

    Empty --> AutoOccupied : Dispatcher.pop(0) type=auto
    Empty --> ManualPending : Dispatcher.pop(0) type=manual
    AutoOccupied --> Empty : Context Flush（success=True）
    AutoOccupied --> Failed : Context Flush（needs_hilp=True）
    Failed --> AutoOccupied : Human Intervention action=retry\n（清除 halt，保留槽）
    Failed --> Empty : Human Intervention action=abort
    ManualPending --> Empty : Human Intervention action=complete\n（清除 halt，清空槽）
    ManualPending --> Empty : Human Intervention action=abort

    note right of Empty : Dispatcher 看到 {} 才会填入新任务
    note right of Failed : Dispatcher 看到非空槽跳过，不覆盖
    note right of ManualPending : Executor 不参与，直接等待操作员
```

---

## 6. @require_robot_active 守卫机制

```mermaid
flowchart TD
    CALL["R-skill 调用<br/>e.g. move_to_target()"]
    CHECK{{"halt_flag == True ?"}}
    BYPASS{{"bypass_halt == True ?"}}
    BLOCK["返回 SkillResult\nsuccess=False\nerror_type=ROBOT_INACTIVE"]
    EXEC3["正常执行 skill"]

    CALL --> CHECK
    CHECK -->|"否"| EXEC3
    CHECK -->|"是"| BYPASS
    BYPASS -->|"否（普通 R-skill）"| BLOCK
    BYPASS -->|"是（白名单）"| EXEC3

    subgraph WHITELIST["白名单（bypass_halt=True）"]
        R["resume()"]
        RHI["request_human_intervention()"]
    end

    EXEC3 -.-> WHITELIST
```
