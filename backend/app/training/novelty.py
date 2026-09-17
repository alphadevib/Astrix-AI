"""Open-world anomaly handling.

The failure-mode catalogue in `agents/knowledge.py` lists seven faults. Anything
outside it currently lands as `unclassified`, selects no candidate actions, and
is dropped on the floor — which is precisely the case that matters once the model
is flying against real hardware, because the fault that takes a spacecraft down
is rarely the one someone wrote a matcher for.

This module closes that gap without inventing authority:

1. **Recognise.** A detection that crossed the alarm threshold but matched no
   catalogued failure mode is *novel*, not *absent*. It gets a stable signature
   derived from which channels deviated and in which direction.

2. **Recall.** The signature is compared against every novel signature already in
   the training corpus and against the mission summaries. If this shape has been
   seen before, whatever was done last time — and whether it worked — comes back
   with it.

3. **Propose.** A recovery is proposed only from `ACTION_CATALOG`. The model may
   decide *which* modelled action fits an unfamiliar fault; it may not invent an
   action, and nothing here executes anything. The proposal goes to the same
   deterministic safety engine, digital twin and approval gate as every other
   recovery, so an open-world diagnosis has exactly the authority a catalogued
   one does and no more.

The conservative default is `increase_monitoring_rate`: when Astrix does not
recognise a fault, gathering more data at higher cadence is the action least
likely to make an unknown situation worse.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

log = logging.getLogger(__name__)

# Fallbacks, in order of preference, for a fault nothing in the catalogue
# explains. Every id must exist in `agents/knowledge.ACTION_CATALOG`.
CONSERVATIVE_ACTIONS = ("increase_monitoring_rate", "enter_safe_mode")


def is_modelled(action_id: str | None) -> bool:
    """True only for actions the digital twin and safety engine can rule on.

    Precedent comes out of the corpus, which is data — an old row, a corpus copied
    from another deployment, or a label written before the catalogue changed can
    all name an action that no longer exists. Proposing one would hand the safety
    engine something it cannot simulate, so an unmodelled precedent is dropped
    rather than passed along.
    """
    if not action_id or action_id == "none":
        return False
    from ..agents.knowledge import ACTION_CATALOG

    return action_id in ACTION_CATALOG

# A novel signature needs this many deviating channels before it is worth
# recording. One channel crossing a threshold is usually sensor noise.
MIN_CHANNELS = 2


def channel_family(name: str) -> str:
    """`wheel_3_vibration` → `wheel_vibration`, so unit 3 and unit 1 share a shape."""
    parts = name.split("_")
    if len(parts) >= 3 and parts[1].isdigit():
        return f"{parts[0]}_{'_'.join(parts[2:])}"
    return name


def signature(deviating: list[str], severity: str = "") -> str:
    """Stable short id for an anomaly shape.

    Built from channel *families* rather than exact channel names so the same
    fault on a different reaction wheel is recognised as the same shape, and
    sorted so channel ordering cannot produce two ids for one fault.
    """
    families = sorted({channel_family(c) for c in deviating if c})
    if not families:
        return ""
    digest = hashlib.sha256(("|".join(families) + f"#{severity}").encode()).hexdigest()
    return f"nov-{digest[:10]}"


def is_novel(diagnosis: dict[str, Any] | None, detection: dict[str, Any]) -> bool:
    """True when this anomaly is real but matches nothing the catalogue knows.

    The test is the diagnosis, not the matcher list: `diagnostic.py` already
    validates any failure mode — its own or the LLM's — against the catalogue and
    writes `unclassified` when nothing fits. An alarm-level detection with several
    deviating channels and no classified mode is exactly the open-world case.
    """
    if detection.get("suppressed"):
        return False
    if (detection.get("severity") or "NORMAL") in ("NORMAL", "WATCH"):
        return False
    mode = (diagnosis or {}).get("failure_mode")
    if mode and mode != "unclassified":
        return False
    return len(detection.get("deviating_parameters") or []) >= MIN_CHANNELS


def describe(detection: dict[str, Any]) -> dict[str, Any]:
    """The novel-anomaly record: what deviated, how far, and what it is called."""
    deviating = list(detection.get("deviating_parameters") or [])
    return {
        "signature": signature(deviating, str(detection.get("severity") or "")),
        "deviating_parameters": deviating,
        "channel_families": sorted({channel_family(c) for c in deviating}),
        "severity": detection.get("severity"),
        "ml_score": round(float(detection.get("ml_score") or 0.0), 3),
        "final_score": round(float(detection.get("final_score") or 0.0), 3),
        "persistence_seconds": round(float(detection.get("persistence_seconds") or 0.0), 1),
    }


def similarity(left: list[str], right: list[str]) -> float:
    """Jaccard overlap of two channel-family sets."""
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class OpenWorldAdvisor:
    """Answers novel anomalies from what past missions did about similar shapes.

    Holds no telemetry of its own: it reads the corpus (mission summaries and
    per-frame examples) and returns a proposal. The caller decides whether to act
    on it, and the safety engine decides whether it may.
    """

    def __init__(self, corpus, threshold: float = 0.42) -> None:
        self.corpus = corpus
        self.threshold = threshold

    def precedents(self, record: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        """Past examples whose deviating channels resemble this one's."""
        families = record.get("channel_families") or []
        if not families:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for example in self._examples():
            situation = example.get("situation") or {}
            past = situation.get("deviating_parameters") or []
            if not past:
                continue
            score = similarity(families, [channel_family(c) for c in past])
            if score >= self.threshold:
                scored.append((score, example))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "similarity": round(score, 3),
                "failure_mode": (example.get("labels") or {}).get("failure_mode"),
                "subsystem": (example.get("labels") or {}).get("subsystem"),
                "action_id": (example.get("labels") or {}).get("action_id"),
                "safety_status": (example.get("labels") or {}).get("safety_status"),
                "scope": example.get("scope", "frame"),
            }
            for score, example in scored[:limit]
        ]

    def propose(self, record: dict[str, Any]) -> dict[str, Any]:
        """A recovery proposal for a novel anomaly, with its provenance.

        `action_id` is always drawn from the modelled action catalogue, never
        invented, and `authority` says plainly that this is advisory.
        """
        matches = self.precedents(record)
        best = next((m for m in matches if is_modelled(m.get("action_id"))), None)

        if best:
            action = best["action_id"]
            rationale = (
                f"No catalogued failure mode matches this signature. The closest precedent "
                f"({best['similarity']:.0%} channel overlap, diagnosed as "
                f"{best.get('failure_mode') or 'unclassified'}) was answered with {action}."
            )
            confidence = round(min(0.75, 0.35 + best["similarity"] * 0.5), 3)
        else:
            severity = str(record.get("severity") or "")
            action = CONSERVATIVE_ACTIONS[1] if severity == "CRITICAL" else CONSERVATIVE_ACTIONS[0]
            rationale = (
                "No catalogued failure mode and no precedent in mission history match this "
                f"signature. Falling back to {action}: the action least likely to worsen an "
                "unrecognised fault."
            )
            confidence = 0.3

        return {
            "novel": True,
            "signature": record.get("signature"),
            "action_id": action,
            "rationale": rationale,
            "confidence": confidence,
            "precedents": matches,
            "authority": "advisory — subject to the safety engine, digital twin and approval gate",
        }

    def _examples(self) -> list[dict[str, Any]]:
        try:
            return self.corpus.recent_examples(400)
        except Exception:  # noqa: BLE001 — learning must never block operations
            log.debug("open-world recall unavailable", exc_info=True)
            return []
