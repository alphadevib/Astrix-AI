"""Multi-Model LLM Gateway with Tiered Routing & Robust Fallback.

Supports:
1. Anthropic Claude (Claude 3.7 Sonnet, Claude 3.5 Haiku)
2. Google Gemini (Gemini 2.0 Flash / Pro via REST / google-genai)
3. Groq / OpenAI / OpenRouter (Llama 3.3, GPT-4o, DeepSeek)
4. Local Offline Models via Ollama (for air-gapped spacecraft ops)

Tiers:
- 'fast': Rapid triage (<100ms)
- 'deep': Multi-variable root cause diagnosis and complex trade-off analysis
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _clean_json_text(text: str) -> str:
    """Strip markdown code fences and extraneous text around JSON."""
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json ... ```
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # If there's still text before the first { or [
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        return match.group(1)
    return text


class ModelGateway:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.calls = 0
        self.failures = 0
        self.active_provider: str | None = None
        self._disabled_reason: str | None = None

        # Resolve credentials
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        self.gemini_key = os.environ.get("GEMINI_API_KEY") or settings.gemini_api_key
        self.groq_key = os.environ.get("GROQ_API_KEY") or settings.groq_api_key
        self.openai_key = os.environ.get("OPENAI_API_KEY") or settings.openai_api_key

        self._anthropic_client = None
        if self.anthropic_key:
            try:
                import anthropic
                self._anthropic_client = anthropic.Anthropic(
                    api_key=self.anthropic_key,
                    timeout=settings.llm_timeout_seconds,
                )
            except Exception as exc:
                log.warning("Anthropic client initialization warning: %s", exc)

        # Check local Ollama availability
        self.ollama_available = False
        try:
            with httpx.Client(timeout=1.0) as client:
                r = client.get("http://localhost:11434/api/tags")
                if r.status_code == 200:
                    self.ollama_available = True
        except Exception:
            pass

        # Decide preferred provider
        norm_provider = settings.llm_provider.lower().strip()
        if norm_provider in ("local", "ollama"):
            self.active_provider = "local"
        elif norm_provider != "auto":
            self.active_provider = norm_provider
        elif self.gemini_key:
            self.active_provider = "gemini"
        elif self.groq_key:
            self.active_provider = "groq"
        elif self.anthropic_key:
            self.active_provider = "anthropic"
        elif self.openai_key:
            self.active_provider = "openai"
        elif self.ollama_available:
            self.active_provider = "local"
        else:
            self.active_provider = "deterministic"
            self._disabled_reason = "No LLM API keys configured (operating in deterministic mode)"

    @property
    def available(self) -> bool:
        return self.s.llm_enabled and self.active_provider not in (None, "deterministic")

    @property
    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.active_provider,
            "model": self.s.llm_model if self.available else None,
            "reason": self._disabled_reason,
            "calls": self.calls,
            "failures": self.failures,
            "providers_detected": {
                "anthropic": bool(self.anthropic_key),
                "gemini": bool(self.gemini_key),
                "groq": bool(self.groq_key),
                "openai": bool(self.openai_key),
                "ollama": self.ollama_available,
            },
        }

    def structured(
        self,
        system: str,
        user: str,
        output_model: type[T],
        label: str = "agent",
        tier: str = "deep",
        max_tokens: int | None = None,
    ) -> T | None:
        """Route structured request through gateway with tiered fallback."""
        if not self.s.llm_enabled:
            return None

        self.calls += 1
        max_tokens = max_tokens or self.s.llm_max_tokens

        # Candidate order based on active provider and fallback availability
        providers_to_try: list[str] = []
        if self.active_provider and self.active_provider != "deterministic":
            providers_to_try.append(self.active_provider)

        for p in ["gemini", "groq", "anthropic", "openai", "local"]:
            if p not in providers_to_try:
                if p == "gemini" and self.gemini_key:
                    providers_to_try.append(p)
                elif p == "groq" and self.groq_key:
                    providers_to_try.append(p)
                elif p == "anthropic" and self._anthropic_client:
                    providers_to_try.append(p)
                elif p == "openai" and self.openai_key:
                    providers_to_try.append(p)

        for provider in providers_to_try:
            try:
                if provider == "anthropic":
                    result = self._call_anthropic(system, user, output_model, tier, max_tokens)
                elif provider == "gemini":
                    result = self._call_gemini(system, user, output_model, tier, max_tokens)
                elif provider == "groq":
                    result = self._call_groq(system, user, output_model, tier, max_tokens)
                elif provider == "openai":
                    result = self._call_openai(system, user, output_model, tier, max_tokens)
                elif provider == "local":
                    result = self._call_local(system, user, output_model, tier, max_tokens)
                else:
                    result = None

                if result is not None:
                    return result
            except Exception as exc:
                self.failures += 1
                log.warning("[%s] Provider '%s' call failed: %s. Trying next provider...", label, provider, exc)

        return None

    def _call_anthropic(
        self,
        system: str,
        user: str,
        output_model: type[T],
        tier: str,
        max_tokens: int,
    ) -> T | None:
        if not self._anthropic_client:
            return None

        model_name = self.s.llm_fast_model if tier == "fast" else self.s.llm_model
        # Ensure valid standard model name if invalid default is passed
        if "opus-5" in model_name:
            model_name = "claude-3-7-sonnet-20250219"

        # Anthropic structured json schema request
        schema = output_model.model_json_schema()
        system_with_schema = (
            f"{system}\n\nCRITICAL: Respond ONLY with a valid JSON object strictly matching this schema:\n"
            f"{json.dumps(schema, indent=2)}\nDo not include any conversational preamble or markdown code blocks."
        )

        kwargs: dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "system": system_with_schema,
            "messages": [{"role": "user", "content": user}],
        }

        # Handle thinking budget properly if model supports it
        if "3-7" in model_name and tier == "deep" and max_tokens > self.s.thinking_budget_tokens + 256:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.s.thinking_budget_tokens,
            }

        response = self._anthropic_client.messages.create(**kwargs)
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if not text:
            return None
        cleaned = _clean_json_text(text)
        return output_model.model_validate_json(cleaned)

    def _call_gemini(
        self,
        system: str,
        user: str,
        output_model: type[T],
        tier: str,
        max_tokens: int,
    ) -> T | None:
        if not self.gemini_key:
            return None
        model_name = "gemini-2.0-flash" if tier == "fast" else "gemini-2.0-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.gemini_key}"

        schema = output_model.model_json_schema()
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": schema,
                "maxOutputTokens": max_tokens,
            },
        }

        with httpx.Client(timeout=self.s.llm_timeout_seconds) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                log.warning("Gemini API error %s: %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            if not text:
                return None
            return output_model.model_validate_json(_clean_json_text(text))

    def _call_groq(
        self,
        system: str,
        user: str,
        output_model: type[T],
        tier: str,
        max_tokens: int,
    ) -> T | None:
        if not self.groq_key:
            return None
        model_name = "llama-3.1-8b-instant" if tier == "fast" else "llama-3.3-70b-versatile"
        url = "https://api.groq.com/openai/v1/chat/completions"

        schema = output_model.model_json_schema()
        messages = [
            {
                "role": "system",
                "content": f"{system}\n\nRespond with valid JSON conforming to: {json.dumps(schema)}",
            },
            {"role": "user", "content": user},
        ]
        payload = {
            "model": model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.groq_key}"}

        with httpx.Client(timeout=self.s.llm_timeout_seconds) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                log.warning("Groq API error %s: %s", resp.status_code, resp.text)
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            return output_model.model_validate_json(_clean_json_text(content))

    def _call_openai(
        self,
        system: str,
        user: str,
        output_model: type[T],
        tier: str,
        max_tokens: int,
    ) -> T | None:
        if not self.openai_key:
            return None
        model_name = "gpt-4o-mini" if tier == "fast" else "gpt-4o"
        url = f"{self.s.openai_api_base.rstrip('/')}/chat/completions"

        schema = output_model.model_json_schema()
        messages = [
            {"role": "system", "content": f"{system}\n\nRespond with valid JSON schema: {json.dumps(schema)}"},
            {"role": "user", "content": user},
        ]
        payload = {
            "model": model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.openai_key}"}

        with httpx.Client(timeout=self.s.llm_timeout_seconds) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            return output_model.model_validate_json(_clean_json_text(content))

    def _call_local(
        self,
        system: str,
        user: str,
        output_model: type[T],
        tier: str,
        max_tokens: int,
    ) -> T | None:
        url = f"{self.s.local_model_url.rstrip('/')}/chat/completions"
        schema = output_model.model_json_schema()
        messages = [
            {
                "role": "system",
                "content": f"{system}\n\nCRITICAL: Respond ONLY with a valid JSON object strictly matching this schema:\n{json.dumps(schema, indent=2)}\nDo not output markdown code blocks or explanatory commentary.",
            },
            {"role": "user", "content": user},
        ]
        model_name = self.s.llm_model
        if not model_name or "claude" in model_name or "gemini" in model_name or "gpt" in model_name:
            model_name = "qwen3:8b"

        payload = {
            "model": model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        timeout = max(45.0, self.s.llm_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                log.warning("Ollama API call failed: %s %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            message = data.get("choices", [{}])[0].get("message", {})
            content = message.get("content") or message.get("reasoning") or ""
            return output_model.model_validate_json(_clean_json_text(content))
