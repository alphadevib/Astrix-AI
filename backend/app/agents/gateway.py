"""Multi-model LLM gateway with runtime provider selection and robust fallback.

Providers live in `providers.py`: Gemini, Groq, Hugging Face and a local Ollama
runtime — the four whose credentials actually return completions — plus Astrix's
own on-board Small LM, which needs no third party at all. Any provider with
credentials configured is usable.

Routing:
- The *active* provider is tried first. It is chosen by `ASTRIX_LLM_PROVIDER`
  ("auto" picks the first configured provider, free tiers first) and can be
  switched at runtime from the dashboard via `select()`.
- If it fails, every other configured provider is tried in registry order.
- A provider that fails repeatedly is benched for a cooldown, so a dead endpoint
  does not add a timeout to every telemetry cycle.
- If nothing answers, callers get `None` and use their deterministic reasoner.

Tiers:
- 'fast': rapid triage on the provider's small model
- 'deep': diagnosis and trade-off analysis on the provider's large model
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ..config import Settings
from .providers import PROVIDER_MAP, PROVIDERS, RETIRED, ProviderSpec

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 60.0
_CLOUD_MODEL_HINTS = ("claude", "gemini", "gpt-", "llama-3.3-70b-versatile")


def _clean_json_text(text: str) -> str:
    """Strip reasoning tags, markdown code fences and extraneous text around JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        return match.group(1)
    return text


def _schema_instruction(output_model: type[BaseModel]) -> str:
    return (
        "CRITICAL: Respond ONLY with a valid JSON object strictly matching this schema:\n"
        f"{json.dumps(output_model.model_json_schema())}\n"
        "Do not include conversational preamble, markdown code fences or commentary."
    )


class ModelGateway:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.calls = 0
        self.failures = 0
        self.last_provider: str | None = None
        self.last_model: str | None = None
        self._lock = threading.Lock()
        self._health: dict[str, dict[str, float]] = {}
        self._selected_model: str | None = None
        self._disabled_reason: str | None = None
        self._ollama_models: list[str] = []
        self.ollama_available = False
        # Astrix's own on-board model, constructed on first use by the
        # `small_lm` property below.
        self._small_lm = None

        # Keys given through ASTRIX_-prefixed settings count too.
        for env_name, value in (
            ("GEMINI_API_KEY", settings.gemini_api_key),
            ("GROQ_API_KEY", settings.groq_api_key),
            ("HF_TOKEN", settings.huggingface_api_key),
        ):
            if value and not os.environ.get(env_name):
                os.environ[env_name] = value

        self.active_provider = self._initial_provider()

    @property
    def small_lm(self):
        if self._small_lm is None:
            from ..training.small_llm import AstrixSmallLM

            self._small_lm = AstrixSmallLM()
        return self._small_lm

    # ------------------------------------------------------------------ #
    # Provider state
    # ------------------------------------------------------------------ #

    def _initial_provider(self) -> str:
        if not self.s.llm_enabled:
            self._disabled_reason = "LLM disabled by configuration"
            return "deterministic"
        wanted = (self.s.llm_provider or "auto").lower().strip()
        if wanted == "ollama":
            wanted = "local"
        if wanted in ("small_llm", "astrix-small-lm", "astrix_small_lm", "small-llm"):
            wanted = "small_llm"
        if wanted in RETIRED:
            # An .env left over from when the registry carried dead providers must
            # not silently pin the console to a reasoner that no longer exists.
            log.warning(
                "ASTRIX_LLM_PROVIDER='%s' is no longer available (%s) — falling back to auto",
                wanted,
                RETIRED[wanted],
            )
            wanted = "auto"
        if wanted in PROVIDER_MAP:
            if wanted == "local":
                self.refresh_local()
                # The configured local model is the operator's choice.
                if self.s.llm_model and not any(h in self.s.llm_model for h in _CLOUD_MODEL_HINTS):
                    self._selected_model = self.s.llm_model
            return wanted
        for spec in PROVIDERS:
            if spec.kind != "ollama" and self.configured(spec):
                return spec.key
        if self.refresh_local():
            return "local"
        self._disabled_reason = "No LLM provider configured (operating in deterministic mode)"
        return "deterministic"

    def refresh_local(self) -> bool:
        try:
            base = self.s.local_model_url.rstrip("/").removesuffix("/v1")
            with httpx.Client(timeout=0.6) as client:
                r = client.get(f"{base}/api/tags")
            if r.status_code == 200:
                self._ollama_models = [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
                self.ollama_available = True
                return True
        except Exception:  # noqa: BLE001
            pass
        self.ollama_available = False
        return False

    def configured(self, spec: ProviderSpec) -> bool:
        if spec.kind == "small_llm":
            return True
        if spec.kind == "ollama":
            return self.ollama_available
        return bool(spec.api_key()) and spec.resolved_base_url() is not None

    def _benched(self, key: str) -> bool:
        h = self._health.get(key)
        return bool(h and h.get("benched_until", 0.0) > time.monotonic())

    def _record(self, key: str, ok: bool) -> None:
        with self._lock:
            h = self._health.setdefault(key, {"ok": 0, "fail": 0, "streak": 0, "benched_until": 0.0})
            if ok:
                h["ok"] += 1
                h["streak"] = 0
            else:
                h["fail"] += 1
                h["streak"] += 1
                if h["streak"] >= FAILURE_THRESHOLD:
                    h["benched_until"] = time.monotonic() + COOLDOWN_SECONDS
                    h["streak"] = 0
                    log.warning("provider '%s' benched for %.0fs after repeated failures", key, COOLDOWN_SECONDS)

    def model_for(self, spec: ProviderSpec, tier: str) -> str:
        if spec.key == self.active_provider and self._selected_model:
            return self._selected_model
        override = os.environ.get(spec.model_env)
        if override:
            return override
        return spec.fast_model if tier == "fast" else spec.deep_model

    def select(self, provider: str, model: str | None = None) -> dict[str, Any]:
        """Switch the active reasoner at runtime ('deterministic' turns the LLM off)."""
        provider = provider.lower().strip()
        if provider == "ollama":
            provider = "local"
        if provider in ("small_llm", "astrix-small-lm", "astrix_small_lm", "small-llm"):
            provider = "small_llm"
        if provider == "deterministic":
            self.active_provider = "deterministic"
            self._selected_model = None
            self._disabled_reason = "Deterministic reasoners selected by the operator"
            return self.status
        spec = PROVIDER_MAP.get(provider)
        if spec is None:
            if provider in RETIRED:
                raise ValueError(f"{provider} was {RETIRED[provider]}")
            raise KeyError(f"unknown provider '{provider}'")
        if spec.kind == "ollama":
            self.refresh_local()
        if not self.configured(spec):
            hint = "start Ollama" if spec.kind == "ollama" else f"set {' or '.join(spec.env_keys)}"
            raise ValueError(f"{spec.label} is not configured — {hint}")
        self.active_provider = provider
        self._selected_model = (model or "").strip() or None
        self._disabled_reason = None
        self._health.pop(provider, None)
        log.info("reasoner switched to %s (%s)", spec.label, self.model_for(spec, "deep"))
        return self.status

    @property
    def available(self) -> bool:
        return self.s.llm_enabled and self.active_provider not in (None, "deterministic")

    @property
    def status(self) -> dict[str, Any]:
        spec = PROVIDER_MAP.get(self.active_provider or "")
        return {
            "available": self.available,
            "provider": self.active_provider,
            "provider_label": spec.label if spec else "Deterministic",
            "model": self.model_for(spec, "deep") if (spec and self.available) else None,
            "reason": self._disabled_reason,
            "calls": self.calls,
            "failures": self.failures,
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "providers_detected": {p.key: self.configured(p) for p in PROVIDERS},
        }

    def catalogue(self) -> list[dict[str, Any]]:
        out = []
        for spec in PROVIDERS:
            models = list(spec.models)
            if spec.kind == "ollama":
                models = list(dict.fromkeys(self._ollama_models + models))
            health = self._health.get(spec.key, {})
            out.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "tier": spec.tier,
                    "free_tier": spec.free_tier,
                    "signup_url": spec.signup_url,
                    "env_keys": list(spec.env_keys) + list(spec.extra_env),
                    "configured": self.configured(spec),
                    "active": spec.key == self.active_provider,
                    "default_model": self.model_for(spec, "deep"),
                    "models": models,
                    "installed_models": self._ollama_models if spec.kind == "ollama" else [],
                    "benched": self._benched(spec.key),
                    "ok": int(health.get("ok", 0)),
                    "failed": int(health.get("fail", 0)),
                }
            )
        return out

    def _order(self) -> list[ProviderSpec]:
        order: list[ProviderSpec] = []
        if self.active_provider in PROVIDER_MAP:
            order.append(PROVIDER_MAP[self.active_provider])
        for spec in PROVIDERS:
            if spec not in order and spec.kind not in ("ollama", "small_llm") and self.configured(spec):
                order.append(spec)
        return [spec for spec in order if not self._benched(spec.key)]

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #

    def structured(
        self,
        system: str,
        user: str,
        output_model: type[T],
        label: str = "agent",
        tier: str = "deep",
        max_tokens: int | None = None,
    ) -> T | None:
        """Route a structured request through the gateway with fallback."""
        if not self.available:
            return None
        self.calls += 1
        max_tokens = max_tokens or self.s.llm_max_tokens
        system_full = f"{system}\n\n{_schema_instruction(output_model)}"
        for spec in self._order():
            model = self.model_for(spec, tier)
            try:
                text = self._complete(spec, model, system_full, [{"role": "user", "content": user}], max_tokens, output_model)
                if not text:
                    self._record(spec.key, False)
                    continue
                result = output_model.model_validate_json(_clean_json_text(text))
                self._record(spec.key, True)
                self.last_provider, self.last_model = spec.key, model
                return result
            except Exception as exc:  # noqa: BLE001
                self.failures += 1
                self._record(spec.key, False)
                log.warning("[%s] provider '%s' (%s) failed: %s — trying next", label, spec.key, model, exc)
        return None

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int = 700) -> dict[str, str] | None:
        """Free-text completion for the operator assistant."""
        if not self.available:
            return None
        self.calls += 1
        for spec in self._order():
            model = self.model_for(spec, "fast")
            try:
                text = self._complete(spec, model, system, messages, max_tokens, None)
                if text:
                    self._record(spec.key, True)
                    self.last_provider, self.last_model = spec.key, model
                    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                    return {"text": cleaned, "provider": spec.key, "model": model}
                self._record(spec.key, False)
            except Exception as exc:  # noqa: BLE001
                self.failures += 1
                self._record(spec.key, False)
                log.warning("[assistant] provider '%s' failed: %s", spec.key, exc)
        return None

    def _complete(
        self,
        spec: ProviderSpec,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        output_model: type[BaseModel] | None,
    ) -> str | None:
        if spec.kind == "small_llm":
            return self._call_small_llm(spec, model, system, messages, max_tokens, output_model)
        if spec.kind == "small_llm":
            return self._call_small_lm(messages)
        if spec.kind == "gemini":
            return self._call_gemini(spec, model, system, messages, max_tokens, output_model)
        if spec.kind == "ollama":
            base = self.s.local_model_url.rstrip("/")
            return self._call_openai_compatible(spec, base, None, model, system, messages, max_tokens, output_model, timeout=max(45.0, self.s.llm_timeout_seconds))
        base = spec.resolved_base_url()
        if not base:
            return None
        return self._call_openai_compatible(spec, base, spec.api_key(), model, system, messages, max_tokens, output_model)

    def _call_small_llm(
        self,
        spec: ProviderSpec,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        output_model: type[BaseModel] | None,
    ) -> str | None:
        user_content = messages[-1]["content"] if messages else ""
        if output_model is None:
            sol = self.small_lm.solve_issue(user_content)
            return (
                f"AstrixSmallLM Diagnostic Decision:\n"
                f"- Subsystem: {sol['decision']['diagnosis']['subsystem']}\n"
                f"- Failure Mode: {sol['decision']['diagnosis']['failure_mode']}\n"
                f"- Cause: {sol['decision']['diagnosis']['probable_cause']}\n"
                f"- Action: {sol['decision']['recovery']['action_id']} ({sol['decision']['recovery']['rationale']})\n"
                f"- Anomaly Filter: {sol['anomaly_filter_verdict']}\n"
                f"- Model Version: {sol['decision']['model_version']}"
            )

        situation = self.small_lm._query_to_situation(user_content)
        decision = self.small_lm.predict(situation)
        fields = output_model.model_fields if hasattr(output_model, "model_fields") else {}

        # 1. Diagnostic Agent (_Draft)
        if "probable_cause" in fields and "evidence" in fields:
            payload = {
                "subsystem": decision["diagnosis"]["subsystem"],
                "component": decision["diagnosis"]["component"],
                "probable_cause": decision["diagnosis"]["probable_cause"],
                "failure_mode": decision["diagnosis"]["failure_mode"],
                "confidence": float(decision["diagnosis"]["confidence"]),
                "evidence": [
                    f"{p} deviation detected"
                    for p in situation.get("deviating_parameters", ["attitude_error_deg"])
                ],
                "ruled_out": [
                    "Sensor spoofing / single-channel transient"
                    if not decision["problem_solving"]["is_sensor_spoof"]
                    else "Genuine physical hardware fault"
                ],
            }
            return json.dumps(payload)

        # 2. Risk Agent (_Draft)
        if "cascading_risks" in fields:
            sev = decision["risk"]["severity"]
            impact = "HIGH" if sev == "CRITICAL" else ("MEDIUM" if sev == "WARNING" else "LOW")
            sub = decision["diagnosis"]["subsystem"]
            payload = {
                "mission_impact": impact,
                "power_risk": "HIGH" if sub == "POWER" else "LOW",
                "attitude_risk": "HIGH" if sub == "ADCS" else "LOW",
                "thermal_risk": "HIGH" if sub == "THERMAL" else "LOW",
                "communication_risk": "HIGH" if sub == "COMMS" else "LOW",
                "time_to_impact_minutes": float(decision["risk"]["time_to_impact_minutes"]),
                "cascading_risks": [f"{sub} {decision['diagnosis']['failure_mode']} propagation risk"],
            }
            return json.dumps(payload)

        # 3. Recovery Agent (_Draft)
        if "ranked_actions" in fields or "selected_action_id" in fields:
            act_id = decision["recovery"]["action_id"]
            rat = decision["recovery"]["rationale"]
            payload = {
                "ranked_actions": [{"action_id": act_id, "rationale": rat}],
                "selected_action_id": act_id,
            }
            return json.dumps(payload)

        # 4. Critic Agent (CriticReview)
        if "confirmation_bias_detected" in fields or "sensor_spoofing_risk" in fields:
            payload = {
                "subsystem": decision["diagnosis"]["subsystem"],
                "primary_cause": decision["diagnosis"]["probable_cause"],
                "confirmation_bias_detected": False,
                "sensor_spoofing_risk": "HIGH" if decision["problem_solving"]["is_sensor_spoof"] else "LOW",
                "confidence_score": float(decision["diagnosis"]["confidence"]),
                "counter_arguments": [decision["problem_solving"]["resolution_notes"]],
                "ruled_out_hypotheses": [],
                "consensus_recommendation": decision["problem_solving"]["recommendation"],
                "evidence_weighting": {"subsystem_coherence": 0.95, "temporal_persistence": 0.9},
            }
            return json.dumps(payload)

        return json.dumps(decision)


    def _call_gemini(
        self,
        spec: ProviderSpec,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        output_model: type[BaseModel] | None,
    ) -> str | None:
        key = spec.api_key()
        if not key:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if output_model is not None:
            config["response_mime_type"] = "application/json"
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": config,
        }
        with httpx.Client(timeout=self.s.llm_timeout_seconds) as client:
            # Header auth keeps the key out of URLs and access logs.
            resp = client.post(url, json=payload, headers={"x-goog-api-key": key})
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts) or None

    def _call_openai_compatible(
        self,
        spec: ProviderSpec,
        base_url: str,
        api_key: str | None,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        output_model: type[BaseModel] | None,
        timeout: float | None = None,
    ) -> str | None:
        url = f"{base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
            "temperature": 0.1 if output_model else 0.4,
        }
        if output_model is not None:
            payload["response_format"] = {"type": "json_object"}
        headers = dict(spec.headers)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(timeout=timeout or self.s.llm_timeout_seconds) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code in (400, 422) and "response_format" in payload:
                # Not every model supports JSON mode; the schema instruction still applies.
                payload.pop("response_format")
                resp = client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        message = resp.json().get("choices", [{}])[0].get("message", {})
        return message.get("content") or message.get("reasoning") or None
