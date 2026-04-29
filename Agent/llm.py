import os
from langchain_core.language_models import BaseChatModel
from dotenv import load_dotenv

load_dotenv(override=True)

# Timeout (seconds) per LLM phase, keyed by provider.
# ollama runs locally and is significantly slower than remote API calls.
_TIMEOUTS: dict[str, dict[str, float]] = {
    "claude": {"supervisor": 60,  "planner": 40, "executor_plan": 40, "executor_recovery": 60},
    "ollama": {"supervisor": 180, "planner": 100, "executor_plan": 60, "executor_recovery": 80},
}


def get_node_timeouts() -> dict[str, float]:
    """Return timeout seconds per node/phase for the active LLM provider."""
    provider = os.getenv("ROBOSKI_LLM_PROVIDER", "claude")
    return _TIMEOUTS.get(provider, _TIMEOUTS["claude"])


def create_llm(provider: str = None, **kwargs) -> BaseChatModel: #typ: ignore
    """LLM factory. Provider is read from ROBOSKI_LLM_PROVIDER env var (default: claude)."""
    provider = provider or os.getenv("ROBOSKI_LLM_PROVIDER", "claude")

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic
        model    = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        # Set HTTP timeout = longest node timeout so the connection closes before
        # the ThreadPoolExecutor wrapper fires, preventing zombie background threads.
        timeout  = max(get_node_timeouts().values())
        return ChatAnthropic(model_name=model, timeout=timeout, **kwargs)

    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        load_dotenv(override=True)  # ensure env vars are reloaded
        model_id = os.getenv("OLLAMA_MODEL_ID", "qwen3:latest")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(model=model_id, base_url=base_url, temperature=0, **kwargs)

    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Set ROBOSKI_LLM_PROVIDER to 'claude' or 'ollama'.")
