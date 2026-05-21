import logging
import os
import pathlib

from langchain_core.language_models import BaseChatModel
from dotenv import load_dotenv

load_dotenv(override=True)

_log = logging.getLogger(__name__)

# Timeout (seconds) per LLM phase, keyed by provider.
# Local providers (ollama, llamacpp) are significantly slower than remote API calls.
_TIMEOUTS: dict[str, dict[str, float]] = {
    "claude":   {"supervisor": 60,  "planner": 40,  "executor_plan": 40, "executor_recovery": 60},
    "ollama":   {"supervisor": 180, "planner": 100, "executor_plan": 60, "executor_recovery": 80},
    "llamacpp": {"supervisor": 240, "planner": 100, "executor_plan": 90, "executor_recovery": 80},
}


def get_node_timeouts() -> dict[str, float]:
    """Return timeout seconds per node/phase for the active LLM provider."""
    provider = os.getenv("ROBOSKI_LLM_PROVIDER", "claude")
    return _TIMEOUTS.get(provider, _TIMEOUTS["claude"])


def _ensure_gguf(repo: str, filename: str, local_dir: str) -> pathlib.Path:
    """Download a GGUF model from HuggingFace if not already cached locally.

    Respects HF_ENDPOINT env var for mirror support (e.g. https://hf-mirror.com).
    Returns the absolute path to the local GGUF file.
    """
    dest_dir = pathlib.Path(local_dir).expanduser().resolve()
    dest = dest_dir / filename

    if dest.exists():
        _log.info("GGUF model ready: %s", dest)
        return dest

    dest_dir.mkdir(parents=True, exist_ok=True)
    _log.info("Model not found locally. Downloading %s from %s ...", filename, repo)
    if os.getenv("HF_ENDPOINT"):
        _log.info("Using HF mirror: %s", os.getenv("HF_ENDPOINT"))

    try:
        from huggingface_hub import hf_hub_download
        hf_hub_download(repo_id=repo, filename=filename, local_dir=str(dest_dir))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download {filename} from {repo}: {exc}\n"
            "Tip: set HF_ENDPOINT=https://hf-mirror.com to use the China mirror."
        ) from exc

    _log.info("Download complete: %s", dest)
    return dest


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

    elif provider == "llamacpp":
        from langchain_openai import ChatOpenAI
        import requests

        base_url   = os.getenv("LLAMACPP_BASE_URL",   "http://localhost:8080/v1")
        model_repo = os.getenv("LLAMACPP_MODEL_REPO", "bartowski/Qwen2.5-14B-Instruct-GGUF")
        model_file = os.getenv("LLAMACPP_MODEL_FILE", "Qwen2.5-14B-Instruct-Q4_K_M.gguf")
        model_dir  = os.getenv("LLAMACPP_MODEL_DIR",  "./models")

        model_path = _ensure_gguf(model_repo, model_file, model_dir)

        # Verify the llama-server is reachable before returning the client.
        try:
            requests.get(f"{base_url}/models", timeout=5).raise_for_status()
        except Exception:
            raise RuntimeError(
                f"llama.cpp server not reachable at {base_url}.\n"
                f"Start it with:\n"
                f"  llama-server -m {model_path} --host 0.0.0.0 --port 8080 -ngl 99\n"
                "Then retry."
            )

        return ChatOpenAI(
            base_url=base_url,
            api_key="na",
            model="local",
            temperature=0,
            **kwargs,
        )

    else:
        raise ValueError(
            f"Unknown LLM provider: {provider!r}. "
            "Set ROBOSKI_LLM_PROVIDER to 'claude', 'ollama', or 'llamacpp'."
        )
