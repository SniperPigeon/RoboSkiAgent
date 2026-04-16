import json
import logging
import random
from pathlib import Path
from typing import Tuple, cast

import agentlightning as agl
from agentlightning import Dataset
from dotenv import load_dotenv
from openai import AsyncOpenAI

from planning_agent import planner_rollout, get_initial_planner_prompt_template, PlannerTask


_HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

def load_dataset(path: str | Path = _HERE / "plan_claude.jsonl") -> list[PlannerTask]:
    """Load all samples from a JSONL dataset file."""
    samples: list[PlannerTask] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def split_dataset(
    samples: list[PlannerTask],
    val_ratio: float = 0.25,
    seed: int = 42,
) -> Tuple[Dataset[PlannerTask], Dataset[PlannerTask]]:
    """Shuffle and split samples into (train, val) agl.Dataset objects.

    Args:
        samples:   Full dataset loaded via load_dataset().
        val_ratio: Fraction reserved for validation (default 0.2).
        seed:      Random seed for reproducibility.

    Returns:
        (train_dataset, val_dataset) as agl.Dataset[PlannerTask]
    """
    if not 0 < val_ratio < 1:
        raise ValueError(f"val_ratio must be in (0, 1), got {val_ratio}")

    shuffled = samples.copy()
    random.Random(seed).shuffle(shuffled)

    n_val   = max(1, round(len(shuffled) * val_ratio))
    n_train = len(shuffled) - n_val
    if n_train < 1:
        raise ValueError(f"Too few samples ({len(samples)}) to split with val_ratio={val_ratio}")

    train = shuffled[n_val:]
    val   = shuffled[:n_val]
    return cast(Dataset[PlannerTask], train), cast(Dataset[PlannerTask], val)


# ---------------------------------------------------------------------------
# Initial prompt
# ---------------------------------------------------------------------------

def make_initial_prompt() -> agl.PromptTemplate:
    return get_initial_planner_prompt_template()


def setup_apo_logger(file_path: str = "apo.log") -> None:
    """Send APO INFO-level logs to both the console and a log file."""
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    # Console: show APO progress in real time
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File: persist full trace
    file_handler = logging.FileHandler(file_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    apo_logger = logging.getLogger("agentlightning")
    apo_logger.setLevel(logging.INFO)
    apo_logger.addHandler(console_handler)
    apo_logger.addHandler(file_handler)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_best_prompt(apo) -> None:
    """Print and save the best prompt found so far."""
    try:
        best = apo.get_best_prompt()
        sep = "=" * 60
        print(f"\n{sep}\nBEST PROMPT (score={apo._history_best_score:.3f}):\n{sep}\n{best.template}\n{sep}")
        out = _HERE / "best_planner_prompt.txt"
        out.write_text(best.template, encoding="utf-8")
        print(f"Saved to {out}")
    except ValueError:
        print("\n[APO] No best prompt recorded yet.")


def main():
    from agentlightning import Trainer
    from agentlightning.adapter.messages import TraceToMessages
    load_dotenv()
    setup_apo_logger()
    train, val = split_dataset(load_dataset())
    print(f"Dataset: {len(train)} train / {len(val)} val")

    # APO uses OpenAI to generate prompt gradients and edits (reads OPENAI_API_KEY from env).
    openai_client = AsyncOpenAI()

    apo = agl.APO(
        async_openai_client=openai_client,
        gradient_model="gpt-4.1-mini",
        apply_edit_model="gpt-4.1-mini",
        beam_width=2,
        branch_factor=2,
        beam_rounds=3,
    )

    trainer = Trainer(
        algorithm=apo,
        initial_resources={"planner_prompt": make_initial_prompt()},
        adapter=TraceToMessages(),
        n_runners=2,
    )

    try:
        trainer.fit(planner_rollout, train_dataset=train, val_dataset=val)
    except KeyboardInterrupt:
        print("\n[APO] Interrupted.")
    finally:
        _print_best_prompt(apo)



if __name__ == "__main__":
    main()
