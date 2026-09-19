"""Reasoner provider registry.

Every provider Astrix can reason with, in the order `auto` prefers them.

Only providers whose credentials answered a live request are listed:

    gemini · groq · huggingface · local (Ollama)

A fine-tuned `astrix-lm` (scripts/train_astrix_lm.py) is served through Ollama.
Adding a provider is a short `ProviderSpec` — but it belongs here only once its
key actually returns a completion.

Most providers speak the OpenAI chat-completions dialect, so one client covers
them; Gemini and Ollama keep dedicated adapters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    kind: str  # "openai" | "gemini" | "ollama"
    env_keys: tuple[str, ...]
    deep_model: str
    fast_model: str
    models: tuple[str, ...]
    tier: str  # "free" | "local"
    free_tier: str
    signup_url: str
    base_url: str = ""
    extra_env: tuple[str, ...] = ()  # non-secret values the base URL needs
    headers: dict[str, str] = field(default_factory=dict)

    def api_key(self) -> str | None:
        for name in self.env_keys:
            value = os.environ.get(name)
            if value:
                return value.strip()
        return None

    def resolved_base_url(self) -> str | None:
        url = self.base_url
        for name in self.extra_env:
            value = os.environ.get(name)
            if not value:
                return None
            url = url.replace("{" + name + "}", value.strip())
        return url

    @property
    def model_env(self) -> str:
        return f"ASTRIX_{self.key.upper()}_MODEL"


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="gemini",
        label="Google Gemini",
        kind="gemini",
        env_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        # "-latest" aliases track Google's current Flash models, so defaults don't go stale.
        deep_model="gemini-flash-latest",
        fast_model="gemini-flash-lite-latest",
        models=("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-3.5-flash-lite"),
        tier="free",
        free_tier="Free tier on Flash models via Google AI Studio, no card required",
        signup_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        key="groq",
        label="Groq",
        kind="openai",
        base_url="https://api.groq.com/openai/v1",
        env_keys=("GROQ_API_KEY",),
        deep_model="openai/gpt-oss-120b",
        fast_model="openai/gpt-oss-20b",
        models=("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "groq/compound-mini"),
        tier="free",
        free_tier="Free plan, ~30 requests/min and ~1,000 requests/day on large models",
        signup_url="https://console.groq.com/keys",
    ),
    ProviderSpec(
        key="huggingface",
        label="Hugging Face Inference",
        kind="openai",
        base_url="https://router.huggingface.co/v1",
        env_keys=("HF_TOKEN", "HUGGINGFACE_API_KEY"),
        deep_model="Qwen/Qwen3-32B",
        fast_model="Qwen/Qwen3-4B-Instruct-2507",
        models=("Qwen/Qwen3-32B", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen3-8B", "Qwen/Qwen3-4B-Instruct-2507"),
        tier="free",
        free_tier="Small monthly inference credit on free accounts",
        signup_url="https://huggingface.co/settings/tokens",
    ),
    ProviderSpec(
        key="local",
        label="Local (Ollama)",
        kind="ollama",
        env_keys=(),
        deep_model="qwen3:8b",
        fast_model="qwen3:8b",
        models=("qwen3:8b", "llama3.2:3b", "gemma3:4b", "phi4-mini", "mistral:7b", "deepseek-r1:8b", "astrix-lm"),
        tier="local",
        free_tier="Runs on your machine; no key, no rate limit, works air-gapped",
        signup_url="https://ollama.com/download",
    ),
)

PROVIDER_MAP: dict[str, ProviderSpec] = {p.key: p for p in PROVIDERS}
