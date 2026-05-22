# RoboSkiAgent Project Summary

本文档用 Mermaid 图概括 RoboSkiAgent 当前主线架构。项目整体可以理解为：

> 自然语言装配任务 -> LangGraph 多节点决策 -> `skill.md` 高层技能展开 -> Genesis primitives 执行 -> sensors 验证与恢复。

当前主线是 V2：Planner 只生成 skill-level `todo_list`，Executor 再根据 `SkiLib/skills/*.md` 动态展开为 primitives 和 perception checks。

## 1. High-Level 项目模块与 Actors

这张图展示外部 actor、Agent 层、LLM provider、SkiLib 技能库，以及 Genesis 仿真后端之间的关系。

```mermaid
flowchart LR
    Operator["Operator / 自然语言输入"] -->|"assembly instruction"| UI["CLI / Gradio GUI"]

    UI -->|"make_initial_state(prompt)"| Graph["Agent Layer<br/>LangGraph StateGraph<br/>graph_v2.py"]

    subgraph LLM["LLM Provider"]
        Claude["Claude API<br/>Anthropic"]
        LlamaCpp["llama.cpp<br/>OpenAI-compatible local server"]
        Ollama["Ollama<br/>local chat model"]
    end

    Graph -->|"create_llm()"| LLM

    subgraph Agent["Agent Nodes"]
        Supervisor["Supervisor<br/>符号场景理解"]
        Planner["Planner V2<br/>skill-level todo_list"]
        PlanReview["Plan Review<br/>human approval"]
        Dispatcher["Dispatcher<br/>task slotting"]
        Executor["Executor V2<br/>skill.md -> primitives/sensors"]
        Manual["Manual Handler<br/>operator action"]
        HITL["HITL Handler<br/>retry / replan / abort"]
    end

    Graph --> Agent

    subgraph SkiLib["SkiLib"]
        RobotContext["RobotContext<br/>Genesis facade"]
        SkillMdLoader["SkillMdLoader<br/>loads skills/*.md"]
        SkillRegistry["SkillRegistry<br/>legacy Python skills"]
        PrimitiveRegistry["PrimitiveRegistry<br/>MoveJ MoveL Grasp Release"]
        SensorRegistry["SensorRegistry<br/>execution-time perception"]
        MetaTools["metatools<br/>symbolic scene info"]
    end

    Supervisor -->|"list_targets/list_objects/list_assembly_recipe"| MetaTools
    Planner -->|"loads skill schemas"| SkillMdLoader
    Executor -->|"loads skill body"| SkillMdLoader
    Executor -->|"action tools"| PrimitiveRegistry
    Executor -->|"check tools"| SensorRegistry
    RobotContext --> PrimitiveRegistry
    RobotContext --> SensorRegistry
    RobotContext --> SkillRegistry

    subgraph GenesisLayer["Genesis Backend"]
        Runtime["GenesisRuntime<br/>scene objects targets tools"]
        Scene["build_genesis_scene()<br/>UR16e + Robotiq + FMB objects"]
        Motion["motion.py<br/>IK + PD control"]
        Controller["GenesisController<br/>viewer thread serializer"]
        Genesis["Genesis Physics Engine<br/>gs.Scene / robot"]
    end

    RobotContext --> Runtime
    Runtime --> Scene
    PrimitiveRegistry --> Motion
    SensorRegistry -->|"read state / object pose"| Runtime
    Motion -->|"scene.step()"| Runtime
    Controller -.->|"viewer mode serializes step()"| Runtime
    Runtime --> Genesis

    Operator <-.->|"approve / complete / retry / replan"| PlanReview
    Operator <-.-> Manual
    Operator <-.-> HITL
```

### 说明

- `Operator` 提供自然语言任务，例如 assemble、pick and place，或更具体的装配指令。
- `CLI / Gradio GUI` 是入口；GUI 支持 plan review、manual task、HITL recovery 等 interrupt 流程。
- `Agent Layer` 使用 LangGraph 组织状态机。当前 GUI 和 CLI 默认走 `graph_v2.py`。
- `LLM Provider` 由 `Agent/llm.py` 选择，可使用 Claude API、Ollama，或 llama.cpp 的 OpenAI-compatible server。
- `SkiLib` 不依赖 LangGraph，负责技能、primitive、sensor、场景符号解析。
- `GenesisRuntime` 持有实际 Genesis scene、robot、objects、targets 和 held-item 状态。

## 2. 简略 Agent Flow

这张图展示一次任务从自然语言输入，到规划、审阅、分发、执行、失败恢复的主流程。

```mermaid
flowchart TD
    Start([Start: user prompt]) --> Supervisor["Supervisor<br/>scene-symbol saturation<br/>任务意图改写 + 可用符号"]

    Supervisor -->|"ok"| Planner["Planner V2<br/>LLM tool calls:<br/>add_PickAndPlace_task / add_manual_task"]
    Supervisor -->|"abort / timeout"| End([End])

    Planner --> PlanReview["Plan Review Interrupt<br/>operator reviews todo_list"]

    PlanReview -->|"approve"| Dispatcher["Dispatcher<br/>pop next task into current_task"]
    PlanReview -->|"replan + feedback"| Supervisor
    PlanReview -->|"abort"| End

    Dispatcher -->|"auto task"| ExecutorPlan["Executor V2: Plan Phase<br/>read skill.md<br/>register_execution_plan<br/>action + check steps"]
    Dispatcher -->|"manual task"| Manual["Manual Handler Interrupt"]
    Dispatcher -->|"no task"| End

    Manual -->|"complete"| Dispatcher
    Manual -->|"abort"| End

    ExecutorPlan --> ExecutorRun["Executor V2: Run Phase<br/>execute primitives<br/>run sensor checks"]
    ExecutorRun -->|"all steps success"| Dispatcher

    ExecutorRun -->|"step/check failure"| Recovery["Executor V2: Recovery Phase<br/>LLM sub-agent uses primitives + sensors"]
    Recovery -->|"recovered"| Dispatcher
    Recovery -->|"unrecoverable / timeout / escalate"| HITL["HITL Handler Interrupt"]

    HITL -->|"retry same task"| ExecutorPlan
    HITL -->|"next_task"| Dispatcher
    HITL -->|"replan"| Supervisor
    HITL -->|"abort"| End
```

### 说明

- `Supervisor` 只做 planning-time symbolic information gathering，不应该依赖物理坐标。
- `Planner V2` 使用 `SkillMdLoader` 从 `skills/*.md` 生成 tool schema，然后通过 LLM tool calls 写入 `todo_list`。
- `Plan Review` 是强制 human gate：operator 可以 approve、replan 或 abort。
- `Dispatcher` 一次只把一个 task 放入 `current_task`。
- `Executor V2` 分三阶段：
  - Plan phase：把高层 skill 变成 primitive/check execution plan。
  - Run phase：按顺序执行 primitives 和 sensors。
  - Recovery phase：失败时启动 LLM sub-agent，使用 primitives/sensors 尝试恢复。
- `HITL Handler` 处理无法自动恢复的失败，可 retry、跳过任务、replan 或 abort。

## 3. 分层 Skill / Tool 图

这张图按抽象层次展示 planning information、robotic skill、perception sensor、primitive 和 Genesis runtime 的关系。

```mermaid
flowchart TB
    NL["Natural Language Task<br/>例: assemble / pick and place"] --> SupLayer

    subgraph SupLayer["Planning Information Layer"]
        Meta["Informative T-skills<br/>metatools/informative.py"]
        ListRecipe["list_assembly_recipe<br/>装配计划/默认装配顺序获取"]
        ListTargets["list_targets"]
        ListObjects["list_objects"]
        ListTools["list_tools"]
        CheckExists["check_item_exists"]
        GripStatePlan["get_gripper_state<br/>symbolic only"]
    end

    Meta --> ListRecipe
    Meta --> ListTargets
    Meta --> ListObjects
    Meta --> ListTools
    Meta --> CheckExists
    Meta --> GripStatePlan

    SupLayer --> PlannerLayer["Planner Layer<br/>生成 skill-level todo_list"]

    subgraph SkillLayer["Robotic Skill Layer"]
        MdSkill["skill.md spec<br/>SkiLib/skills/pick_and_place.md"]
        PickPlace["PickAndPlace<br/>item, home_position,<br/>pick/place approach/target,<br/>motion mode, grasp_profile"]
        PySkill["legacy Python BaseSkill<br/>SkiLib/skills/*.py"]
    end

    PlannerLayer -->|"add_PickAndPlace_task"| PickPlace
    MdSkill --> PickPlace
    PySkill -.->|"V1 compatibility"| PickPlace

    PickPlace --> ExecLayer["Executor V2<br/>skill guide -> concrete execution plan"]

    subgraph PerceptionLayer["Perception / Sensor Layer"]
        Attach["get_attachment_state"]
        IsGrasped["is_item_grasped"]
        ObjPos["get_object_position<br/>position + is_placed"]
        IsPlaced["is_placed"]
        PickPose["compute_pick_pose<br/>dynamic recovery pick target"]
    end

    ExecLayer -->|"post-action checks / recovery"| PerceptionLayer

    subgraph PrimitiveLayer["Primitive Layer"]
        MoveJ["MoveJ<br/>joint-space motion"]
        MoveL["MoveL<br/>Cartesian linear motion"]
        Grasp["Grasp<br/>weld constraint attach"]
        Release["Release<br/>detach / place"]
    end

    ExecLayer -->|"action steps"| PrimitiveLayer

    subgraph RuntimeLayer["Genesis Runtime Layer"]
        Resolver["Symbol Resolver<br/>target/object/tool names"]
        IK["solve_ik"]
        Control["control_to_qpos"]
        State["physics state<br/>object pose / held_item / gripper"]
        SceneStep["scene.step()"]
    end

    MoveJ --> Resolver
    MoveL --> Resolver
    Grasp --> Resolver
    Release --> Resolver

    MoveJ --> IK
    MoveL --> IK
    IK --> Control
    Control --> SceneStep
    Grasp --> State
    Release --> State
    PerceptionLayer --> State
    PickPose --> Resolver
```

### 说明

- `Planning Information Layer` 是 Supervisor 使用的 T-skills，核心约束是只返回符号信息，不暴露坐标、矩阵或关节值。
- `Robotic Skill Layer` 当前生产技能主要是 `PickAndPlace`。V2 通过 `SkiLib/skills/pick_and_place.md` 描述参数、标准执行序列、验证点和恢复策略。
- `Perception / Sensor Layer` 是 Executor 的 execution-time observation tools，可读取物理状态，例如是否抓住、是否放置成功、物体当前位置和动态 pick pose。
- `Primitive Layer` 是平台绑定的低层动作，目前包括 `MoveJ`、`MoveL`、`Grasp`、`Release`。
- `Genesis Runtime Layer` 负责符号解析、IK、控制循环、`scene.step()` 和物理状态维护。

## 当前主线摘要

- `Agent/graph_v2.py`：当前 LangGraph 主线，拓扑与 V1 类似，但替换为 `planner_v2` 和 `executor_v2`。
- `Agent/nodes/planner_v2.py`：从 `SkillMdLoader` 生成 planner tools，输出 skill-level `todo_list`。
- `Agent/nodes/executor_v2.py`：读取 skill markdown body，先生成 execution plan，再执行 primitives 和 sensors，失败时进入 recovery。
- `SkiLib/skill_loader.py`：解析 `SkiLib/skills/*.md` 的 YAML frontmatter 和 markdown body。
- `SkiLib/robotcontext.py`：Genesis runtime facade，并初始化 primitive、skill、sensor registries。
- `SkiLib/genesis/runtime.py`：持有 Genesis scene、robot、targets、objects、tools，以及 grasp/release 和 placement 相关状态。
- `SkiLib/metatools/informative.py`：Supervisor 的 planning-time scene information tools。
- `SkiLib/sensors/*.py`：Executor 的 execution-time perception tools。
- `SkiLib/primitives/*.py`：Genesis 绑定的底层 robot primitives。

