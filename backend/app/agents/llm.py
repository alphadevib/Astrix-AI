"""LLM bridge and Multi-Model Gateway adapter for reasoning agents.

Design rule for the whole project: **the LLM is optional at every call site.**
Every agent has a deterministic reasoner that produces a valid result on its own,
and this class is allowed to return `None` at any time — no key configured, rate
limited, network down, malformed output, refusal. The caller then falls back and
records `reasoner=DETERMINISTIC` on the result so the dashboard shows which path ran.

Structured output is enforced via Pydantic model validation.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import Settings
from .gateway import ModelGateway

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class AgentLLM:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.gateway = ModelGateway(settings)
        self.calls = 0
        self.failures = 0
        self._disabled_reason: str | None = None

        if not settings.llm_enabled:
            self._disabled_reason = "disabled by configuration (ASTRIX_LLM_ENABLED=false)"

    # -- status ------------------------------------------------------------- #

    @property
    def available(self) -> bool:
        return self.s.llm_enabled and self.gateway.available

    @property
    def status(self) -> dict[str, Any]:
        st = self.gateway.status
        st["calls"] = self.calls
        st["failures"] = self.failures
        if self._disabled_reason:
            st["reason"] = self._disabled_reason
        return st

    # -- inference ---------------------------------------------------------- #

    def structured(
        self,
        system: str,
        user: str,
        output_model: type[T],
        label: str = "agent",
        max_tokens: int | None = None,
        tier: str = "deep",
    ) -> T | None:
        """Return a validated `output_model`, or None if the LLM path is unusable."""
        if not self.available:
            return None

        self.calls += 1
        try:
            result = self.gateway.structured(
                system=system,
                user=user,
                output_model=output_model,
                label=label,
                tier=tier,
                max_tokens=max_tokens or self.s.llm_max_tokens,
            )
            if result is None:
                self.failures += 1
            return result
        except Exception as exc:
            self.failures += 1
            log.warning("[%s] LLM gateway inference failed: %s (falling back to deterministic)", label, exc)
            return None
