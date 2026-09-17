"""LLM bridge for the reasoning agents.

Design rule for the whole project: **the LLM is optional at every call site.**
Every agent has a deterministic reasoner that produces a valid result on its own,
and this class is allowed to return `None` at any time — no key configured, rate
limited, network down, malformed output, refusal. The caller then falls back and
records `reasoner=DETERMINISTIC` on the result so the dashboard shows which path
ran. A hackathon demo that dies because Wi-Fi dropped is a failed demo.

Structured output is not optional, though. Agents ask for a Pydantic model via
`client.messages.parse()`, so anything that reaches the pipeline is schema-valid
by construction. There is no prose parsing anywhere in ASTRIX.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Claude Opus 5 may decline a request via stop_reason="refusal" (HTTP 200).
# Server-side fallback routes those to another model automatically rather than
# leaving an agent with nothing; the deterministic reasoner is the backstop if
# the installed SDK predates the parameter.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _has_credential() -> bool:
    """Best-effort check so startup reports availability honestly.

    An unset `ANTHROPIC_API_KEY` does not by itself mean there are no
    credentials — the SDK also resolves `ANTHROPIC_AUTH_TOKEN` and an
    `ant auth login` profile on disk. This is a heuristic to avoid announcing
    "LLM enabled" and then falling back on the first frame; the first real call
    remains the authority, and failure there disables the path cleanly.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config_home = os.environ.get("XDG_CONFIG_HOME")
    roots = [Path(config_home) if config_home else Path.home() / ".config"]
    return any((root / "anthropic").exists() for root in roots)


class AgentLLM:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._client = None
        self._anthropic = None
        self._disabled_reason: str | None = None
        self.calls = 0
        self.failures = 0

        if not settings.llm_enabled:
            self._disabled_reason = "disabled by configuration (ASTRIX_LLM_ENABLED=false)"
            return
        try:
            import anthropic
        except ImportError:
            self._disabled_reason = "anthropic SDK not installed (pip install anthropic)"
            return

        self._anthropic = anthropic

        if not _has_credential():
            self._disabled_reason = (
                "no API credential found — set ANTHROPIC_API_KEY or run `ant auth login`"
            )
            return

        try:
            # Credentials resolve from the environment: ANTHROPIC_API_KEY,
            # ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile. Construction
            # succeeding does not prove a usable credential exists — the first
            # call does, and an auth failure there disables this permanently.
            self._client = anthropic.Anthropic(timeout=settings.llm_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — never let client setup break startup
            self._disabled_reason = f"client initialisation failed: {exc}"

    # -- status ------------------------------------------------------------- #

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def status(self) -> dict:
        return {
            "available": self.available,
            "model": self.s.llm_model if self.available else None,
            "reason": self._disabled_reason,
            "calls": self.calls,
            "failures": self.failures,
        }

    def _disable(self, reason: str) -> None:
        log.warning("LLM reasoning disabled for this session: %s", reason)
        self._disabled_reason = reason
        self._client = None

    # -- inference ---------------------------------------------------------- #

    def structured(
        self,
        system: str,
        user: str,
        output_model: type[T],
        label: str = "agent",
        max_tokens: int | None = None,
    ) -> T | None:
        """Return a validated `output_model`, or None if the LLM path is unusable."""
        if self._client is None:
            return None

        anthropic = self._anthropic
        assert anthropic is not None
        self.calls += 1

        kwargs = {
            "model": self.s.llm_model,
            "max_tokens": max_tokens or self.s.llm_max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            # Diagnosis and planning are exactly the "remotely complicated"
            # case adaptive thinking exists for.
            "thinking": {"type": "adaptive"},
        }

        try:
            return self._parse(kwargs, output_model)
        except anthropic.AuthenticationError as exc:
            self.failures += 1
            self._disable(f"authentication failed ({exc.__class__.__name__})")
        except anthropic.NotFoundError as exc:
            # Not permanent: a 404 can be transient (model rollout, regional
            # availability). This cycle falls back; the next one tries again.
            self.failures += 1
            log.warning(
                "[%s] model '%s' not found (%s) — using the deterministic reasoner",
                label,
                self.s.llm_model,
                exc,
            )
        except anthropic.RateLimitError:
            self.failures += 1
            log.warning("[%s] rate limited — using the deterministic reasoner", label)
        except anthropic.APIStatusError as exc:
            self.failures += 1
            log.warning("[%s] API error %s — using the deterministic reasoner", label, exc.status_code)
        except anthropic.APIConnectionError as exc:
            self.failures += 1
            log.warning("[%s] connection error (%s) — using the deterministic reasoner", label, exc)
        except (ValidationError, json.JSONDecodeError) as exc:
            self.failures += 1
            log.warning("[%s] model output failed schema validation: %s", label, exc)
        except TypeError as exc:
            # The SDK raises TypeError (not AuthenticationError) when it cannot
            # resolve a credential at all, which is the common "no API key
            # configured" case. It will fail identically on every subsequent
            # call, so disable the path rather than retrying once per frame.
            self.failures += 1
            if "authentication" in str(exc).lower():
                self._disable(
                    "no API credential found — set ANTHROPIC_API_KEY or run `ant auth login`"
                )
            else:
                self._disable(f"incompatible SDK call: {exc}")
        except Exception as exc:  # noqa: BLE001
            # Last resort. An unknown failure repeated once per telemetry frame
            # would be far worse than falling back for the rest of the session:
            # the deterministic reasoners produce a valid result either way.
            self.failures += 1
            self._disable(f"unexpected reasoning backend error: {exc.__class__.__name__}: {exc}")
        return None

    def _parse(self, kwargs: dict, output_model: type[T]) -> T | None:
        client = self._client
        assert client is not None

        # Preferred path: the SDK validates the response against the Pydantic
        # model for us and hands back a typed instance.
        if hasattr(client.messages, "parse"):
            call = dict(kwargs, output_format=output_model)
            try:
                response = client.messages.parse(
                    **call, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            except TypeError as exc:
                # Installed SDK predates server-side refusal fallbacks. Only
                # retry for that specific cause — a TypeError from anywhere else
                # (notably credential resolution) must propagate to the handler.
                if "unexpected keyword argument" not in str(exc):
                    raise
                response = client.messages.parse(**call)
            if getattr(response, "stop_reason", None) == "refusal":
                log.warning("request declined by safety classifiers; falling back")
                return None
            return response.parsed_output

        # Older SDK: constrain the format ourselves and validate on this side.
        response = client.messages.create(
            **kwargs,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": output_model.model_json_schema(),
                }
            },
        )
        if getattr(response, "stop_reason", None) == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None
        return output_model.model_validate_json(text)
