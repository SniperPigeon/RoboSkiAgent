"""CLI entry point: python -m Agent "把 Part_A_1 放到目标点"."""
import argparse
import os
import sys

from dotenv import load_dotenv


def _setup_env() -> None:
    """Load .env and auto-enable LangSmith tracing if API key is present."""
    load_dotenv()
    if os.getenv("LANGSMITH_API_KEY") and not os.getenv("LANGSMITH_TRACING"):
        os.environ["LANGSMITH_TRACING"] = "true"
    if os.getenv("LANGSMITH_TRACING", "false").lower() == "true":
        print(f"[env] LangSmith tracing enabled (project: {os.getenv('LANGSMITH_PROJECT', '(default)')})")


def main():
    _setup_env()

    parser = argparse.ArgumentParser(description="RoboSkiAgent CLI")
    parser.add_argument("prompt", nargs="?", help="Assembly instruction in natural language")
    parser.add_argument("--skip-check", action="store_true",
                        help="Skip IK/collision checks (simulation mode)")
    args = parser.parse_args()

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    from SkiLib.sim_env import setup_robot_env
    # args.skip_check=True forces on; False falls back to ROBOSKI_SKIP_CHECK env var
    setup_robot_env(debug_skip_check=True if args.skip_check else None)

    from Agent.graph import build_graph, make_initial_state
    from Agent.nodes.supervisor import reset_supervisor_cache
    reset_supervisor_cache()

    graph = build_graph()
    config = {"configurable": {"thread_id": "cli-run"}}

    print(f"\n=== Running: {args.prompt!r} ===\n")
    final = graph.invoke(make_initial_state(args.prompt), config=config) #type:ignore

    print("\n=== execution_log ===")
    for line in final.get("execution_log", []):
        print(" ", line)

    print("\n=== todo_list remaining ===", final.get("todo_list"))
    print("=== current_task ===",         final.get("current_task"))
    print("=== halt_flag ===",            final.get("halt_flag"))


if __name__ == "__main__":
    main()
