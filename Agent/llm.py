import os
from langchain_core.language_models import BaseChatModel
from dotenv import load_dotenv

load_dotenv()


def create_llm(provider: str = None, **kwargs) -> BaseChatModel: #typ: ignore
    """LLM factory. Provider is read from ROBOSKI_LLM_PROVIDER env var (default: claude)."""
    provider = provider or os.getenv("ROBOSKI_LLM_PROVIDER", "claude")

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        return ChatAnthropic(model=model, **kwargs)

    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        load_dotenv()  # ensure env vars are reloaded
        model_id = os.getenv("OLLAMA_MODEL_ID", "qwen3:latest")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(model=model_id, base_url=base_url, temperature=0, **kwargs)

    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Set ROBOSKI_LLM_PROVIDER to 'claude' or 'ollama'.")
