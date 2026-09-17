"""Diagnostic Agent (master context §8.3).

Answers "what is actually wrong", with evidence and a confidence score. Two
reasoners, same output contract:

* **LLM** — reasons over the telemetry digest, the score breakdown, the failure-mode
  catalogue and retrieved mission history. Good at weighing ambiguous, partially
  corroborated evidence and at noticing that two failure modes look alike.
* **Deterministic** — matches the failure-mode catalogue directly. Always available.

The LLM's `failure_mode` is validated against the catalogue. If it invents one,
we keep its prose but mark the mode unclassified rather than letting an unknown
string flow into recovery planning, where it would select no candidate actions.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..core.enums import ReasonerKind, Subsystem
from ..core.schemas import DetectionResult, Diagnosis, MemoryRecall, TelemetryFrame
from ..ml.context import implicated_subsystem
from .knowledge import FAILURE_MODES, catalogue_for_prompt, match_failure_modes
from .llm import AgentLLM
from .prompting import ASTRIX_ROLE, detection_summary, frame_summary, recall_summary

log = logging.getLogger(__name__)

_KNOWN_MODES = {mode.key for mode in FAILURE_MODES}

SYSTEM = f"""{ASTRIX_ROLE}

You are the DIAGNOSTIC AGENT. Given telemetry, a detection score breakdown and
retrieved mission history, identify the most probable fault.

Method:
1. Decide which subsystem the evidence implicates, and say what you ruled out and why.
2. Pick the failure mode from the catalogue whose signature best matches. Failure modes
   that present similarly must be separated on their distinguishing channel — for example
   wheel degradation and gyro drift both grow pointing error, but only wheel degradation
   raises wheel vibration and motor current.
3. Cite specific channels and values as evidence.
4. Weigh retrieved history: a close match with a known outcome raises confidence; an
   absence of precedent is not itself evidence of anything.

{catalogue_for_prompt()}"""


class _Draft(BaseModel):
    """LLM-facing schema. Excludes `reasoner`, which the caller sets."""

    subsystem: Subsystem = Field(description="Subsystem the evidence implicates")
    component: str | None = Field(
        default=None, description="Specific component, e.g. reaction_wheel_3"
    )
    probable_cause: str = Field(description="One or two sentences naming the physical cause")
    failure_mode: str | None = Field(
        default=None, description="A failure-mode key from the catalogue, or null if none fits"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(description="Specific channels and values supporting the diagnosis")
    ruled_out: list[str] = Field(
        default_factory=list, description="Alternative explanations considered and rejected, with why"
    )


class DiagnosticAgent:
    name = "diagnostic"

    def __init__(self, llm: AgentLLM) -> None:
        self.llm = llm

    def run(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        recall: MemoryRecall | None = None,
    ) -> Diagnosis:
        historical_match = bool(recall and recall.best_similarity >= 0.3)

        draft = self.llm.structured(
            system=SYSTEM,
            user="\n\n".join(
                [
                    frame_summary(frame),
                    detection_summary(detection),
                    recall_summary(recall),
                    "Diagnose the fault.",
                ]
            ),
            output_model=_Draft,
            label=self.name,
        )
        if draft is not None:
            failure_mode = draft.failure_mode
            if failure_mode and failure_mode not in _KNOWN_MODES:
                log.info(
                    "diagnostic agent proposed unknown failure mode '%s'; keeping prose, "
                    "marking unclassified",
                    failure_mode,
                )
                failure_mode = None
            return Diagnosis(
                subsystem=draft.subsystem,
                component=draft.component,
                probable_cause=draft.probable_cause,
                failure_mode=failure_mode,
                confidence=draft.confidence,
                evidence=draft.evidence,
                ruled_out=draft.ruled_out,
                historical_match=historical_match,
                reasoner=ReasonerKind.LLM,
            )

        return self._deterministic(frame, detection, recall, historical_match)

    # ------------------------------------------------------------------ #

    def _deterministic(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        recall: MemoryRecall | None,
        historical_match: bool,
    ) -> Diagnosis:
        matches = match_failure_modes(frame)

        if not matches:
            subsystem = implicated_subsystem(detection.deviating_parameters)
            return Diagnosis(
                subsystem=subsystem,
                probable_cause=(
                    f"Unclassified deviation in {subsystem.value}. No catalogued failure mode "
                    f"matches the current signature; the anomaly is real by score but its "
                    f"mechanism is not identified."
                ),
                failure_mode=None,
                confidence=max(0.25, min(0.5, detection.final_score)),
                evidence=[
                    f"{param} beyond 3 sigma of the nominal envelope"
                    for param in detection.deviating_parameters[:5]
                ]
                or ["context-adjusted risk score elevated without a single dominant channel"],
                historical_match=historical_match,
                reasoner=ReasonerKind.DETERMINISTIC,
            )

        mode, match = matches[0]
        # Any other matching mode is a genuine alternative we are not selecting;
        # naming it is more honest than silently dropping it.
        alternatives = [
            f"{other.title} (lower confidence {other_match.confidence:.2f})"
            for other, other_match in matches[1:3]
        ]
        confidence = match.confidence
        if historical_match:
            confidence = min(0.95, confidence + 0.06)

        return Diagnosis(
            subsystem=mode.subsystem,
            component=match.component,
            probable_cause=f"{mode.title}. {mode.description}",
            failure_mode=mode.key,
            confidence=round(confidence, 3),
            evidence=match.evidence,
            ruled_out=match.ruled_out + alternatives,
            historical_match=historical_match,
            reasoner=ReasonerKind.DETERMINISTIC,
        )
