# RoboSkiAgent

Accepts natural-language assembly instructions and drives an industrial robot through a multi-agent LangGraph state machine — with mandatory human-in-the-loop gates at every decision boundary.

## Architecture

```
User Instruction
      │
      ▼
 Supervisor ──(T-skills)──► scene facts
      │
      ▼
  Planner ──(dynamic tools)──► todo_list
      │
      ▼
 plan_review ── [HITL: approve / replan / abort]
      │ approve
      ▼
 Dispatcher ──► auto task ──► Executor ──► success ──► Dispatcher (loop)
             └► manual task ─► ManualHandler ─[HITL: complete/abort]
                                              Executor failure ──► HITLHandler
                                               [HITL: retry / next_task / replan / abort]
```

**Two-layer design:**
- **Layer 1 (Planning):** Supervisor queries the RoboDK scene (symbols only, no coordinates), Planner builds a `todo_list` via tool calls.
- **Layer 2 (Execution):** Dispatcher slots tasks, Executor runs skills; failures trigger an LLM recovery loop, then escalate to human if unresolved.

## Directory Structure

```
RoboSkiAgent/
├── Agent/                  # Orchestration layer (LangGraph)
│   ├── graph.py            # build_graph() — state machine assembly
│   ├── state.py            # GlobalState TypedDict
│   ├── llm.py              # LLM factory (claude / ollama)
│   ├── gui.py              # Gradio UI — full interrupt support ✅
│   ├── __main__.py         # CLI entry — no interrupt support ⚠️
│   ├── prompts/            # supervisor.txt / planner.txt / executor.txt
│   └── nodes/              # supervisor, planner, plan_review, dispatcher,
│                           #   executor, manual_handler, hitl_handler
└── SkiLib/                 # Skill library (no LangGraph dependency)
    ├── base.py             # BasePrimitive / BaseSkill / SkillResult
    ├── registry.py         # SkillRegistry singleton (auto-scans skills/)
    ├── robotcontext.py     # RoboDK connection singleton
    ├── log.py              # get_logger() factory
    ├── metatools/          # T-skills: read-only scene queries for Supervisor
    ├── primitives/         # MoveJ, MoveL, Grasp, Release
    └── skills/             # PickAndPlace (8-step sequence)
```

## Tech Stack

| Component | Choice |
|-----------|--------|
| Agent orchestration | LangGraph (`StateGraph`) |
| LLM framework | LangChain Core |
| LLM (default) | Claude (`claude-sonnet-4-6`) |
| LLM (local) | Ollama (`ChatOllama`) |
| Robot simulation | RoboDK |
| UI | Gradio |
| Language | Python 3.11+ |

## Prerequisites

- Python 3.11+
- [RoboDK](https://robodk.com/download) installed and running
- Anthropic API key **or** a local Ollama instance

## Installation

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd RoboSkiAgent

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

## Configuration

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
```

Minimum required fields in `.env`:

```env
# LLM provider: "claude" (default) or "ollama"
ROBOSKI_LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...

# For local Ollama:
# ROBOSKI_LLM_PROVIDER=ollama
# OLLAMA_MODEL_ID=qwen3:latest

# Optional: LangSmith tracing
# LANGSMITH_TRACING=true
# LANGSMITH_API_KEY=lsv2_...
# LANGSMITH_PROJECT=robo-ski-agent
```

## Usage

### GUI (recommended — full interrupt support)

```bash
python -m Agent.gui
```

Opens a Gradio interface at `http://localhost:7860`. Type an assembly instruction and interact with all human-in-the-loop gates (plan review, manual tasks, failure recovery).

### CLI (limited)

```bash
python -m Agent "把 Part_A_1 放到 Tray_1_Place"
```

> **⚠️ CLI limitation:** The CLI uses `graph.invoke()` which raises `NodeInterrupt` when the graph hits any `interrupt()` node (`plan_review`, `hitl_handler`, `manual_intervention_handler`). Until a streaming + resume loop is implemented in `__main__.py`, **the CLI cannot complete flows that require human approval**. Use the GUI for end-to-end runs.

Add `--skip-check` to bypass IK/collision pre-checks in simulation:

```bash
python -m Agent "把 Part_A_1 放到 Tray_1_Place" --skip-check
```

## Logging

Logs are written to both the console and `logs/roboski.log` (rotating, 10 MB × 5 files).
Control verbosity via `ROBOSKI_LOG_LEVEL` (default: `INFO`).
