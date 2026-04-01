# RoboSkiAgent — Architecture Diagrams

> Last updated: 2026-03-30 (rewritten to reflect Phase 6 production code)
>
> Four levels of detail — use the level that matches your audience.

---

## Level 1 · System Overview

High-level: what the system receives, what it produces, who is involved.

```mermaid
flowchart LR
    U(["👤 Operator"])
    U -->|"Natural language\ninstruction"| AG

    subgraph AG["Agent Layer · LangGraph"]
        direction TB
        S["🔍 Supervisor"] --> P["📋 Planner"]
        P --> PR["⏸ Plan Review"]
        PR --> D["⚙️ Dispatcher"]
        D -->|auto| E["🤖 Executor"]
        D -->|manual| MH["⏸ Manual Handler"]
        E -->|failure| HH["⏸ HITL Handler"]
    end

    AG -->|"skill.try_execute()"| SL["SkiLib\nPickAndPlace · MoveJ/L · Grasp/Release"]
    SL -->|"RoboDK API"| R(["🤖 RoboDK"])
    U <-.->|"approve / retry / replan"| PR & MH & HH
```

---

## Level 2 · LangGraph State Machine

All nodes, edges, and HITL interrupt gates (⏸ = `langgraph.interrupt()`).

```mermaid
flowchart TD
    START(["▶ START"]) --> supervisor

    supervisor["🔍 Supervisor\nReAct loop · T-skill queries\nknowledge saturation → SupervisorOutput"]
    supervisor --> planner

    planner["📋 Planner\ndynamic add_&lt;Skill&gt;_task tools\nLLM builds todo_list via tool calls"]
    planner --> plan_review

    plan_review{{"⏸ Plan Review\nshows full todo_list summary\napprove / replan / abort"}}
    plan_review -->|approve| dispatcher
    plan_review -->|"replan\n+ feedback → HumanMessage"| supervisor
    plan_review -->|abort| END1(["⏹ END"])

    dispatcher["⚙️ Dispatcher  (pure code)\npop slot only when current_task = {}\nroute by task.type"]
    dispatcher -->|"type = auto"| executor
    dispatcher -->|"type = manual\nhalt_flag = True"| manual_intervention_handler
    dispatcher -->|"todo_list empty"| END2(["⏹ END"])

    executor["🤖 Executor\n1. skill.try_execute(**params)\n2. LLM recovery loop on failure\n3. escalate_to_hitl if unrecoverable"]
    executor -->|"SkillResult.success = True\nclear current_task"| dispatcher
    executor -->|"_EscalateHITLException\nhalt_flag = True"| hitl_handler

    manual_intervention_handler{{"⏸ Manual Handler\ntask.description shown to operator\ncomplete / abort"}}
    manual_intervention_handler -->|"complete\nclear current_task"| dispatcher
    manual_intervention_handler -->|abort| END3(["⏹ END"])

    hitl_handler{{"⏸ HITL Handler\nshows error_type + suggestion\nretry / next_task / replan / abort"}}
    hitl_handler -->|"retry\nkeep current_task"| executor
    hitl_handler -->|"next_task\nclear current_task"| dispatcher
    hitl_handler -->|"replan\nclear todo_list → HumanMessage"| supervisor
    hitl_handler -->|"abort\nclear all"| END4(["⏹ END"])

    style plan_review              fill:#fef3c7,stroke:#f59e0b
    style manual_intervention_handler fill:#fef3c7,stroke:#f59e0b
    style hitl_handler             fill:#fef3c7,stroke:#f59e0b
    style supervisor               fill:#dbeafe,stroke:#3b82f6
    style planner                  fill:#dbeafe,stroke:#3b82f6
    style executor                 fill:#dbeafe,stroke:#3b82f6
    style dispatcher               fill:#d1fae5,stroke:#10b981
```

---

## Level 3 · SkiLib Internal Layers

Dependency flow from Agent down to RoboDK API calls.

```mermaid
graph TB
    subgraph Agent["Agent Layer (LangGraph)"]
        EXE["🤖 Executor"]
        SUP["🔍 Supervisor"]
    end

    subgraph SkiLib["SkiLib · Skill Library  (zero LangGraph dependency)"]
        subgraph Reg["Registry / Context"]
            SR["SkillRegistry\nauto-scans skills/\nexposes get_tools()"]
            RC["RobotContext\nRoboDK connection singleton\ninjects robot + RDK"]
        end
        subgraph Skills["Skills Layer  ❌ no robodk import"]
            PaP["PickAndPlace\n8-step sequence\nREQUIRED_PRIMITIVES declared"]
        end
        subgraph Prims["Primitives Layer  ✅ robodk import"]
            MJ["MoveJ\njoint motion"] & ML["MoveL\nlinear motion"]
            GR["Grasp\nAttachClosest"] & RE["Release\nDetachAll"]
        end
    end

    RDK(["🤖 RoboDK"])

    EXE -->|"get_skill(name)"| SR
    SUP -->|"get_tools() — T-skills\n(metatools/informative.py)"| SR
    SR -->|"instantiates via\nREQUIRED_PRIMITIVES"| PaP
    PaP -->|uses| MJ & ML & GR & RE
    RC -->|"injects context at startup"| MJ & ML & GR & RE
    MJ & ML & GR & RE -->|"RoboDK API"| RDK

    style Agent  fill:#dbeafe,stroke:#3b82f6
    style Skills fill:#d1fae5,stroke:#10b981
    style Prims  fill:#fce7f3,stroke:#ec4899
    style Reg    fill:#fef3c7,stroke:#f59e0b
```

---

## Level 4 · Single Task Execution Sequence

End-to-end trace for one `PickAndPlace` task, including failure/recovery branch.

```mermaid
sequenceDiagram
    actor Op as 👤 Operator
    participant SUP as Supervisor
    participant PLAN as Planner
    participant PR as Plan Review ⏸
    participant DISP as Dispatcher
    participant EXE as Executor
    participant PaP as PickAndPlace (SkiLib)
    participant RDK as RoboDK

    Op->>SUP: "Place Part_A on Tray_1"

    Note over SUP: ReAct loop — knowledge saturation
    SUP->>RDK: list_targets()
    RDK-->>SUP: ["Part_A_Pick", "Approach_Part_A_Pick", "Tray_1_Place", ...]
    SUP->>RDK: list_objects()
    RDK-->>SUP: ["Part_A"]
    SUP->>PLAN: SupervisorOutput {task_intent, scene}

    Note over PLAN: dynamic tool calls
    PLAN->>PLAN: add_PickAndPlace_task(pick_target="Part_A_Pick",\nplace_target="Tray_1_Place", ...)
    PLAN->>PR: todo_list = [t1: PickAndPlace]

    PR-->>Op: ⏸ Plan summary shown
    Op->>PR: "approve"
    PR->>DISP: approved

    DISP->>EXE: current_task = {skill: PickAndPlace, params: ...}
    EXE->>PaP: try_execute(pick_target, place_target, pick_approach, place_approach)

    Note over PaP: 8-step execution sequence
    PaP->>RDK: MoveL → Approach_Part_A_Pick
    PaP->>RDK: MoveL → Part_A_Pick
    PaP->>RDK: Grasp  (AttachClosest)
    PaP->>RDK: MoveL → Approach_Part_A_Pick  (retract)
    PaP->>RDK: MoveL → Approach_Tray_1_Place (transit)
    PaP->>RDK: MoveL → Tray_1_Place
    PaP->>RDK: Release (DetachAll)
    PaP->>RDK: MoveL → Approach_Tray_1_Place (retract)
    PaP-->>EXE: SkillResult(success=True)

    EXE-->>DISP: success → clear current_task
    DISP-->>DISP: todo_list empty → END

    rect rgb(254, 243, 199)
        Note over EXE,RDK: Failure branch (e.g. IK_FAILURE)
        PaP-->>EXE: SkillResult(success=False, error_type="IK_FAILURE",\nsuggestion="Try approaching from above")
        Note over EXE: LLM recovery loop
        EXE->>EXE: create_agent → analyze error
        EXE->>EXE: escalate_to_hitl("IK_FAILURE", suggestion=...)
        EXE-->>Op: ⏸ HITL Handler — retry / next_task / replan / abort
        Op->>EXE: "retry"
        EXE->>PaP: try_execute(...) — retry same task
    end
```

---

## GlobalState Field Map

Which node reads / writes each field.

```mermaid
flowchart LR
    subgraph STATE["GlobalState (LangGraph shared state)"]
        direction TB
        MSG["messages\nAnnotated append-only"]
        TODO["todo_list"]
        CUR["current_task\nexecution slot: {} = idle"]
        LAST["last_result: SkillResult"]
        HALT["halt_flag: bool"]
        HR["halt_reason"]
        LOG["execution_log\nAnnotated append-only"]
    end

    SUP["Supervisor"] -->|write AIMessage| MSG
    PLAN["Planner"] -->|write| TODO
    PLAN -->|write| LOG

    DISP["Dispatcher"] -->|pop → write| CUR
    DISP -->|update| TODO
    DISP -->|manual: set True| HALT
    DISP -->|manual: MANUAL_TASK| HR
    DISP -->|write| LOG

    EXE["Executor"] -->|write| LAST
    EXE -->|success: clear {}| CUR
    EXE -->|failure: set True| HALT
    EXE -->|failure: TASK_FAILURE| HR
    EXE -->|write| LOG

    HH["HITL Handler\nManual Handler"] -->|clear False| HALT
    HH -->|clear None| HR
    HH -->|"complete/abort: clear {}"| CUR
    HH -->|abort: clear| TODO
    HH -->|write| LOG
    HH -->|replan: write HumanMessage| MSG
```
