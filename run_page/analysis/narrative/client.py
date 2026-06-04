"""LLM client adapters for the narrative layer.

Three implementations:
  - `AnthropicClient`: real Anthropic Messages API (works with Anthropic
    + Anthropic-compatible proxies via ANTHROPIC_BASE_URL).
  - `OpenAICompatibleClient`: any service exposing /v1/chat/completions
    (OpenAI proper, DeepSeek, Zhipu, DashScope, Ollama local, etc.).
  - `FakeClient`: deterministic in-memory mock for tests.

We do NOT add a retry loop here. Per SPEC §6.5, failures should propagate
up and the generator returns None. Adding retries would mask configuration
issues and risk burning rate limits.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .models import CompletionResult, NarrativeClient

log = logging.getLogger(__name__)


# ---------- real client: Anthropic ----------


class AnthropicClient:
    """Anthropic Messages API. SDK reads ANTHROPIC_BASE_URL env var, so any
    Anthropic-compatible proxy (your minimaxi.com, OpenRouter, one-api, …)
    works with the same code.
    """

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", timeout: float = 120.0):
        if not api_key:
            raise ValueError("AnthropicClient: api_key is required")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = None  # lazy

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "anthropic SDK not installed. Run: pip install anthropic"
                ) from exc
            # anthropic.Anthropic() reads ANTHROPIC_BASE_URL from env at init.
            self._client = anthropic.Anthropic(
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._client

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> CompletionResult:
        client = self._ensure_client()
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        )
        usage = msg.usage
        # input_tokens is the canonical field on Anthropic SDK; some
        # OpenAI-shaped proxies rename it. Try both.
        prompt_tokens = (
            getattr(usage, "input_tokens", None)
            or getattr(usage, "prompt_tokens", 0)
        )
        completion_tokens = (
            getattr(usage, "output_tokens", None)
            or getattr(usage, "completion_tokens", 0)
        )
        return CompletionResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model,
        )


# ---------- real client: OpenAI-compatible ----------


class OpenAICompatibleClient:
    """Any service that exposes POST /v1/chat/completions.

    Covers: OpenAI proper, DeepSeek, Zhipu GLM, Alibaba DashScope
    (Qwen), local Ollama, and any other OpenAI-compatible proxy. The
    `base_url` parameter is the only thing that varies.

    Constructor takes an optional `_client` arg so tests can inject a
    fake without monkey-patching the openai SDK.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: float = 120.0,
        *,
        _client: Any = None,  # test injection
    ):
        if not api_key and _client is None:
            raise ValueError("OpenAICompatibleClient: api_key is required")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self._injected = _client  # test path

    def _ensure_client(self):
        if self._injected is not None:
            return self._injected
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK not installed. Run: pip install openai"
            ) from exc
        return openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> CompletionResult:
        client = self._ensure_client()
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        # OpenAI shape: choices[0].message.content, usage.{prompt,completion}_tokens
        text = (resp.choices[0].message.content or "")
        usage = resp.usage
        prompt_tokens = (
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "input_tokens", 0)
        )
        completion_tokens = (
            getattr(usage, "completion_tokens", None)
            or getattr(usage, "output_tokens", 0)
        )
        return CompletionResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model,
        )


# ---------- test fake ----------


class FakeClient:
    """Deterministic in-memory client. Returns canned result, or raises.

    Usage in tests:
        client = FakeClient(
            canned=CompletionResult(text="## 🎯 花絮\\n...", model="fake"),
        )
        result = client.complete(...)
    """

    def __init__(
        self,
        canned: CompletionResult | None = None,
        raise_exc: BaseException | None = None,
    ):
        self.canned = canned or CompletionResult(text="## 🎯 花絮\nplaceholder")
        self.raise_exc = raise_exc
        self.calls: list[dict] = []  # for assertion

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> CompletionResult:
        self.calls.append({"system_chars": len(system), "user_chars": len(user), "max_tokens": max_tokens})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.canned


# ---------- factory (per SPEC §6.3) ----------


# Well-known base URLs to give helpful error messages
_KNOWN_OPENAI_ENDPOINTS = {
    "https://api.openai.com/v1": "OpenAI",
    "https://api.deepseek.com/v1": "DeepSeek",
    "https://open.bigmodel.cn/api/paas/v4": "Zhipu GLM",
    "https://dashscope.aliyuncs.com/compatible-mode/v1": "Aliyun DashScope (Qwen)",
    "http://localhost:11434/v1": "Ollama (local)",
}


def get_client(provider: str | None = None) -> NarrativeClient:
    """Pick a client based on NARRATIVE_PROVIDER env var (default: anthropic).

    Raises with a human-readable error if the required API key is missing
    or the provider name is unknown. Lazy-imports the SDKs so unused
    providers don't fail at module import.
    """
    p = (provider or os.environ.get("NARRATIVE_PROVIDER", "anthropic")).lower().strip()

    if p == "fake":
        return FakeClient()

    if p in ("anthropic", "claude"):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "NARRATIVE_PROVIDER=anthropic requires ANTHROPIC_API_KEY.\n"
                "Set it, or switch providers via NARRATIVE_PROVIDER=openai."
            )
        return AnthropicClient(
            api_key=api_key,
            model=os.environ.get("NARRATIVE_MODEL", "claude-haiku-4-5"),
        )

    if p in ("openai", "openai_compatible", "deepseek", "zhipu", "ollama", "dashscope"):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            hints = ", ".join(sorted(_KNOWN_OPENAI_ENDPOINTS))
            raise ValueError(
                f"NARRATIVE_PROVIDER={p} requires OPENAI_API_KEY.\n"
                f"Set it, and optionally OPENAI_BASE_URL. Known endpoints:\n  {hints}"
            )
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        # For specific aliases, fill in a sensible default base_url if user didn't.
        if p == "deepseek" and "OPENAI_BASE_URL" not in os.environ:
            base_url = "https://api.deepseek.com/v1"
        elif p == "zhipu" and "OPENAI_BASE_URL" not in os.environ:
            base_url = "https://open.bigmodel.cn/api/paas/v4"
        elif p == "dashscope" and "OPENAI_BASE_URL" not in os.environ:
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        elif p == "ollama" and "OPENAI_BASE_URL" not in os.environ:
            base_url = "http://localhost:11434/v1"
        return OpenAICompatibleClient(
            api_key=api_key,
            base_url=base_url,
            model=os.environ.get("NARRATIVE_MODEL", "gpt-4o-mini"),
        )

    raise ValueError(
        f"unknown NARRATIVE_PROVIDER: {p!r}.\n"
        f"Supported: anthropic, openai, deepseek, zhipu, ollama, dashscope, fake"
    )
