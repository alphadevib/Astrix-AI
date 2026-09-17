"""Astrix-LM: capture verified decisions and train Astrix's own models.

Three things feed the corpus, and all three go through the same encrypted,
hash-chained store:

* **Frame examples** — one per anomaly cycle that reached a diagnosis.
* **Mission summaries** — one per completed mission, folding its whole arc into
  a single example (`mission_summary.py`).
* **Novel anomalies** — detections that matched nothing in the failure-mode
  catalogue, captured with their signature so the next occurrence has a
  precedent to recall (`novelty.py`).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from .corpus import TrainingCorpus, build_example
from .mission_summary import (
    NARRATIVE_EVENTS,
    build_mission_summary,
    render_narrative,
    summary_to_example,
)
from .nano import NanoModel
from .novelty import OpenWorldAdvisor, describe, is_novel
from .small_llm import AstrixSmallLM

log = logging.getLogger(__name__)


class ModelLab:
    """Wires the corpus to the event bus and retrains the nano model as data grows."""

    def __init__(
        self,
        path: str,
        key: str | None,
        auto_train_every: int,
        bus=None,
        *,
        mission_summaries: bool = True,
        open_world: bool = True,
        novelty_threshold: float = 0.42,
    ) -> None:
        self.corpus = TrainingCorpus(path, key)
        self.nano = NanoModel(self.corpus, Path(path).parent / "models")
        self.small_lm = AstrixSmallLM(Path(path).parent / "models" / "astrix_small_lm.joblib")
        self.auto_train_every = max(0, auto_train_every)
        self.mission_summaries_enabled = mission_summaries
        self.open_world_enabled = open_world
        self.advisor = OpenWorldAdvisor(self.corpus, novelty_threshold)
        self.last_auto_train: dict[str, Any] | None = None
        self.last_mission_summary: dict[str, Any] | None = None
        self.novel_seen: dict[str, int] = {}
        self.bus = bus
        # The bus's own replay buffer is capped at 200 events, far short of a
        # mission; the journal below is the mission's own record, reset each launch.
        self._journal: list[dict[str, Any]] = []
        if bus is not None:
            bus.tap({"cycle"}, self._on_cycle)
            bus.tap(NARRATIVE_EVENTS, self._on_narrative)

    # -- capture ------------------------------------------------------------ #

    def _on_cycle(self, _event_type: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        self._maybe_novel(payload)
        example = build_example(payload)
        if example is None:
            return
        row_id = self.corpus.add(example)
        if row_id is None:
            return  # duplicate situation/decision
        self.nano.shadow_compare(example)
        if self.auto_train_every and self.corpus.count() % self.auto_train_every == 0:
            threading.Thread(target=self._auto_train, name="astrix-nano-train", daemon=True).start()

    def _maybe_novel(self, cycle: dict[str, Any]) -> None:
        """Record and answer an anomaly the failure-mode catalogue does not cover."""
        if not self.open_world_enabled:
            return
        detection = cycle.get("detection") or {}
        diagnosis = cycle.get("diagnosis") or {}
        try:
            if not is_novel(diagnosis, detection):
                return
            record = describe(detection)
            if not record.get("signature"):
                return
            proposal = self.advisor.propose(record)
            self.novel_seen[record["signature"]] = self.novel_seen.get(record["signature"], 0) + 1
            if self.bus is not None:
                self.bus.publish(
                    "novel_anomaly",
                    {
                        **record,
                        "occurrences": self.novel_seen[record["signature"]],
                        "proposed_action": proposal["action_id"],
                        "rationale": proposal["rationale"],
                        "confidence": proposal["confidence"],
                        "precedents": proposal["precedents"],
                        "authority": proposal["authority"],
                        "spacecraft_id": cycle.get("spacecraft_id"),
                        "cycle_id": cycle.get("cycle_id"),
                    },
                )
            # Captured as its own example so the next occurrence has a precedent.
            self.corpus.add(
                {
                    "scope": "novel",
                    "situation": {
                        "severity": record["severity"],
                        "ml_score": record["ml_score"],
                        "final_score": record["final_score"],
                        "persistence_seconds": record["persistence_seconds"],
                        "deviating_parameters": record["deviating_parameters"],
                        "context_factors": {
                            f.get("name"): round(float(f.get("value", 0.0)), 3)
                            for f in detection.get("context_factors", [])
                        },
                    },
                    "decision": {
                        "diagnosis": {
                            "subsystem": diagnosis.get("subsystem") or "UNKNOWN",
                            "failure_mode": "novel",
                            "probable_cause": proposal["rationale"],
                            "confidence": proposal["confidence"],
                        },
                        "recovery": {"action_id": proposal["action_id"], "rationale": proposal["rationale"]},
                        "safety_status": "ADVISORY",
                    },
                    "labels": {
                        "subsystem": diagnosis.get("subsystem") or "UNKNOWN",
                        "failure_mode": record["signature"],
                        "action_id": proposal["action_id"],
                        "reasoner": "OPEN_WORLD",
                        "safety_status": "ADVISORY",
                    },
                    "cycle_id": cycle.get("cycle_id"),
                    "spacecraft_id": cycle.get("spacecraft_id"),
                }
            )
        except Exception:  # noqa: BLE001 — learning must never block operations
            log.exception("open-world capture failed")

    def _on_narrative(self, event_type: str, payload: Any) -> None:
        """Keep the mission's own event journal. A new launch starts a new one."""
        if event_type == "mission_started":
            self._journal = []
        self._journal.append({"type": event_type, "payload": payload})
        del self._journal[:-4000]

    def record_mission(
        self, mission: dict[str, Any], events: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """Fold a finished mission into one training example. Called by the runner."""
        if not self.mission_summaries_enabled:
            return None
        try:
            summary = build_mission_summary(events if events is not None else self._journal, mission)
            summary["narrative"] = render_narrative(summary)
            self.last_mission_summary = summary
            example = summary_to_example(summary)
            if example is not None:
                self.corpus.add(example)
                log.info("mission summary captured: %s", summary["findings"]["verdict"])
            return summary
        except Exception:  # noqa: BLE001
            log.exception("mission summary capture failed")
            return None

    def _auto_train(self) -> None:
        try:
            self.last_auto_train = self.nano.train()
        except Exception:  # noqa: BLE001
            log.exception("automatic nano-model training failed")

    def status(self) -> dict[str, Any]:
        return {
            "corpus": self.corpus.stats(),
            "nano": self.nano.status(),
            "small_lm": self.small_lm.status(),
            "auto_train_every": self.auto_train_every,
            "last_auto_train": self.last_auto_train,
            "mission_summaries": {
                "enabled": self.mission_summaries_enabled,
                "last": self.last_mission_summary,
            },
            "open_world": {
                "enabled": self.open_world_enabled,
                "signatures_seen": len(self.novel_seen),
                "occurrences": sum(self.novel_seen.values()),
            },
        }


__all__ = [
    "AstrixSmallLM",
    "ModelLab",
    "NanoModel",
    "OpenWorldAdvisor",
    "TrainingCorpus",
    "build_example",
    "build_mission_summary",
    "render_narrative",
    "summary_to_example",
]
