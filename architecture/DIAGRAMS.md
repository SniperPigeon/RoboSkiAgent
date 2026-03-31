# RoboSkiAgent — Architecture Diagrams

---

## 1. System Overview

High-level view: from natural language to robot action.

```mermaid
flowchart TD
    NL["Natural Language\nInstruction"]

    subgraph Agent ["Agent Layer  (LangGraph)"]
        direction TB
        SUP["Supervisor\nScene query & intent resolution"]
        PLAN["Planner\nBuild symbolic task sequence"]
        PR["Plan Review\n⏸ Human approval gate"]
        DISP["Dispatcher\nRoute tasks deterministically"]
        EXEC["Executor\nInvoke skill + LLM recovery loop"]

        SUP --> PLAN --> PR
        PR -- approve --> DISP
        PR -- replan --> SUP
        DISP -- auto task --> EXEC
    end

    subgraph HITL ["Human-in-the-Loop"]
        MAN["Manual Task Handler\ncomplete / abort"]
        ERR["HITL Handler\nretry / next / replan / abort"]
    end

    subgraph SkiLib ["SkiLib  (Skill Library)"]
        direction TB
        REG["SkillRegistry"]
        SK["Skills\nPickAndPlace · …"]
        PRIM["Primitives\nMoveJ · MoveL · Grasp · Release"]
        CTX["RobotContext  (singleton)"]

        REG --> SK --> PRIM --> CTX
    end

    ROBOT["🤖 RoboDK / Real Robot"]

    NL --> SUP
    DISP -- manual task --> MAN
    EXEC -- unrecoverable --> ERR
    ERR -- retry --> EXEC
    ERR -- replan --> SUP
    EXEC -- success --> DISP
    EXEC --> REG
    CTX --> ROBOT
```

---

## 2. Agent Graph (LangGraph Nodes & Edges)

State machine inside the Agent layer.

```mermaid
stateDiagram-v2
    [*] --> supervisor

    supervisor --> planner
    planner --> plan_review : todo_list ready

    plan_review --> dispatcher  : approve
    plan_review --> supervisor  : replan
    plan_review --> [*]         : abort

    dispatcher --> executor          : auto task
    dispatcher --> manual_handler    : manual task
    dispatcher --> [*]               : todo_list empty

    executor --> dispatcher    : success
    executor --> hitl_handler  : unrecoverable failure

    manual_handler --> dispatcher : complete
    manual_handler --> [*]        : abort

    hitl_handler --> executor   : retry
    hitl_handler --> dispatcher : next task
    hitl_handler --> supervisor : replan
    hitl_handler --> [*]        : abort
```

---

## 3. SkiLib Layer Structure

Dependency direction is strictly top-down; no upward imports.

```mermaid
flowchart TB
    subgraph Agent ["Agent (orchestration)"]
        EX["Executor Node"]
    end

    subgraph SkiLib ["SkiLib (skill library — no LangGraph)"]
        direction TB
        SR["SkillRegistry\nauto-scan skills/"]
        SK["BaseSkill\nPickAndPlace · …\n(platform-agnostic)"]
        BP["BasePrimitive\nMoveJ · MoveL · Grasp · Release\n(RoboDK-specific)"]
        RC["RobotContext\nRDK connection · PrimitiveRegistry"]
    end

    RDK["RoboDK API"]

    EX -- get_tools() --> SR
    SR --> SK
    SK --> BP
    BP --> RC
    RC --> RDK

    style Agent fill:#dbeafe
    style SkiLib fill:#dcfce7
```

---

## 4. Global State & Data Flow

How `GlobalState` fields move through the pipeline.

```mermaid
flowchart LR
    SUP(["Supervisor"])
    PLAN(["Planner"])
    PR(["Plan Review"])
    DISP(["Dispatcher"])
    EXEC(["Executor"])

    SUP -->|"messages\n(scene facts)"| PLAN
    PLAN -->|"todo_list\n[task, ...]"| PR
    PR -->|"todo_list confirmed"| DISP
    DISP -->|"current_task\n{skill, params}"| EXEC
    EXEC -->|"last_result\n(SkillResult)"| DISP

    NOTE["execution_log\nappended by every node\n→ displayed in GUI"]

    style NOTE fill:#fef9c3
```

---

## 5. HITL Interrupt Points

Where the system pauses and waits for the human operator.

```mermaid
flowchart TD
    PR["⏸ Plan Review\nShow full todo_list summary"]
    MAN["⏸ Manual Task Handler\nOperator performs physical step"]
    HITL["⏸ HITL Handler\nShow SkillResult error + suggestion"]

    PR -->|approve / replan / abort| A[" "]
    MAN -->|complete / abort| B[" "]
    HITL -->|retry / next_task / replan / abort| C[" "]

    style PR   fill:#fde68a
    style MAN  fill:#fde68a
    style HITL fill:#fde68a
```
