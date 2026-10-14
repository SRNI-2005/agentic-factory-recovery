"""Narrow LLM boundary (spec §3.3, §9).

Exactly three nodes call LLMClient.complete(); everything else in the
pipeline is deterministic. Real providers sit behind the same protocol so
tests inject FakeLLMClient with canned responses.
"""
import os
from typing import Protocol

from coe.config import get_settings


class LLMConfigError(RuntimeError):
    """Missing LLM_PROVIDER/LLM_MODEL — setup error, never mid-run (§9)."""


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> str: ...


def require_llm_config(settings) -> None:
    if not settings.llm_provider or not settings.llm_model:
        raise LLMConfigError(
            "set LLM_PROVIDER and LLM_MODEL before running recoveries "
            "(pre-flight check, spec §9)")


class _LangChainClient:
    """Adapter: a langchain BaseChatModel behind our two-arg protocol."""

    def __init__(self, model) -> None:
        self._model = model

    def complete(self, *, system: str, user: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        msg = self._model.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)])
        content = msg.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            content = "".join(parts)
        return content or ""


def make_llm_client(settings=None) -> LLMClient:
    s = settings or get_settings()
    require_llm_config(s)
    provider = s.llm_provider.lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {}
        if os.environ.get("LLM_BASE_URL"):      # vLLM/Ollama/gateways
            kwargs["base_url"] = os.environ["LLM_BASE_URL"]
        return _LangChainClient(ChatOpenAI(
            model=s.llm_model, temperature=s.llm_temperature, **kwargs))
    if provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = {}
        if s.google_api_key:
            kwargs["api_key"] = s.google_api_key
        return _LangChainClient(ChatGoogleGenerativeAI(
            model=s.llm_model, temperature=s.llm_temperature, **kwargs))
    raise LLMConfigError(f"unsupported LLM_PROVIDER {provider!r} "
                         "(supported: openai, gemini)")
