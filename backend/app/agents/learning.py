"""Mission Learning / Lessons Agent (master context §8.10, §10, §15).

Closes the loop: converts a resolved anomaly into knowledge future missions
retrieve. This is where "today's anomaly becomes tomorrow's knowledge" is either
true or marketing.

Two constraints on what it is allowed to write, both about defensibility:

* **No causal claims.** Cross-mission observations are recorded as
  "associated with" / "potential latent risk" with a confidence below 0.6 and
  `validation_status=WEAKLY_CORRELATED_UNVALIDATED`, unless an outcome
  experimentally established the mechanism (§3).
* **Repeats strengthen, they do not multiply.** `MissionMemory.record_learning`
  merges an identical lesson into the existing row and increments `occurrences`.
  A lesson seen twice becomes `recurring`, and recurring lessons are what surface
  as latent risks on the spacecraft profile.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..core.enums import Outcome, ReasonerKind
from ..core.schemas import (
    DetectionResult,
    Diagnosis,
    Lesson,
    MemoryRecall,
    MissionLearning,
    RecoveryOption,
    SimulationResult,
    TelemetryFrame,
)
from .llm import AgentLLM
from .prompting import ASTRIX_ROLE, detection_summary, diagnosis_summary, recall_summary

SYSTEM = f"""{ASTRIX_ROLE}

You are the MISSION LEARNING AGENT. An anomaly has been handled. Extract what
future missions should know.

Method:
1. Write lessons that would change a future decision. "The wheel degraded" is not a
   lesson; "vibration and motor current rose together ~18 minutes before pointing error,
   so plan isolation at the paired signal" is.
2. Where you see a pattern across missions, record it as a potential, weakly correlated
   risk. Use "associated with", give it confidence below 0.6, and do not assert a
   mechanism you cannot support. Causality requires experimental validation.
3. Separate confirmed limitations (demonstrated by this outcome) from unresolved risks
   (still open after the recovery).
4. Use category values from: precursor_pattern, recovery_effectiveness, latent_risk,
   limitation, operational_constraint, false_alarm_reduction."""


class _LessonDraft(BaseModel):
    category: str = Field(description="One of the categories listed in the instructions")
    description: str = Field(description="The lesson, written so it can change a future decision")
    evidence: list[str] = Field(description="Specific observations supporting it")
    confidence: float = Field(ge=0.0, le=1.0)
    recurring: bool = Field(default=False, description="True if seen across more than one mission")


class _Draft(BaseModel):
    lessons: list[_LessonDraft]
    new_limitations: list[str] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    latent_patterns: list[str] = Field(
        default_factory=list,
        description="Cross-mission patterns, phrased as potential/weakly correlated risks",
    )


_VALID_CATEGORIES = {
    "precursor_pattern",
    "recovery_effectiveness",
    "latent_risk",
    "limitation",
    "operational_constraint",
    "false_alarm_reduction",
}

# Any lesson in this category is an observation, not a finding. Confidence is
# capped so an eager model cannot promote a correlation into a fact.
_UNVALIDATED_CONFIDENCE_CAP = 0.6


class LearningAgent:
    name = "learning"

    def __init__(self, llm: AgentLLM) -> None:
        self.llm = llm

    def run(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        diagnosis: Diagnosis,
        executed: RecoveryOption | None,
        outcome: Outcome,
        effectiveness: float,
        simulation: SimulationResult | None = None,
        recall: MemoryRecall | None = None,
    ) -> MissionLearning:
        action_text = (
            f"Executed action: {executed.action_id} — {executed.description}. "
            f"Expected effect: {executed.expected_effect}."
            if executed
            else "No recovery action was executed."
        )
        simulation_text = (
            f"Digital twin verdict: {simulation.status.value}. {simulation.summary}"
            if simulation
            else "No simulation was run."
        )

        draft = self.llm.structured(
            system=SYSTEM,
            user="\n\n".join(
                [
                    detection_summary(detection),
                    diagnosis_summary(diagnosis),
                    f"RECOVERY\n\n  {action_text}\n  {simulation_text}\n"
                    f"  Recorded outcome: {outcome.value} (effectiveness {effectiveness:.2f})",
                    recall_summary(recall),
                    "Extract the lessons learned from this event.",
                ]
            ),
            output_model=_Draft,
            label=self.name,
        )

        if draft is not None:
            lessons = [
                self._sanitise(item, frame.spacecraft_id)
                for item in draft.lessons
                if item.description.strip()
            ]
            return MissionLearning(
                mission_id=frame.mission_id,
                lessons=lessons,
                recovery_effectiveness=outcome,
                new_limitations=draft.new_limitations,
                unresolved_risks=draft.unresolved_risks,
                latent_patterns=draft.latent_patterns,
                reasoner=ReasonerKind.LLM,
            )

        return self._deterministic(frame, detection, diagnosis, executed, outcome, effectiveness)

    # ------------------------------------------------------------------ #

    def _sanitise(self, draft: _LessonDraft, spacecraft_id: str) -> Lesson:
        category = draft.category.strip().lower()
        if category not in _VALID_CATEGORIES:
            category = "operational_constraint"

        confidence = draft.confidence
        validation = "VALIDATED_BY_OUTCOME"
        if category == "latent_risk":
            confidence = min(confidence, _UNVALIDATED_CONFIDENCE_CAP)
            validation = "WEAKLY_CORRELATED_UNVALIDATED"
        elif draft.recurring:
            validation = "OBSERVED_MULTIPLE_MISSIONS"
        elif confidence < 0.6:
            validation = "OBSERVED"

        return Lesson(
            spacecraft_id=spacecraft_id,
            category=category,
            description=draft.description.strip(),
            evidence=draft.evidence,
            confidence=round(confidence, 3),
            validation_status=validation,
            recurring=draft.recurring,
        )

    def _deterministic(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        diagnosis: Diagnosis,
        executed: RecoveryOption | None,
        outcome: Outcome,
        effectiveness: float,
    ) -> MissionLearning:
        lessons: list[Lesson] = []
        evidence_base = [
            f"{frame.mission_id} @ {frame.timestamp.isoformat()}",
            f"context-adjusted risk score {detection.final_score:.2f} "
            f"({detection.severity.value})",
        ]

        if detection.suppressed:
            lessons.append(
                Lesson(
                    spacecraft_id=frame.spacecraft_id,
                    category="false_alarm_reduction",
                    description=(
                        f"An ML anomaly score of {detection.ml_score:.2f} in "
                        f"{frame.mode.value} mode was not corroborated by context or by any "
                        f"independent sensor, and was correctly downgraded rather than escalated."
                    ),
                    evidence=evidence_base + [detection.suppression_reason or ""],
                    confidence=0.7,
                    validation_status="OBSERVED",
                )
            )
        else:
            precursors = ", ".join(detection.deviating_parameters[:4]) or "no single dominant channel"
            subject = diagnosis.component or diagnosis.subsystem.value
            fault = (diagnosis.failure_mode or "this fault class").replace("_", " ")
            lessons.append(
                Lesson(
                    spacecraft_id=frame.spacecraft_id,
                    category="precursor_pattern",
                    description=(
                        f"{fault.capitalize()} on {subject} was preceded by deviation on: "
                        f"{precursors}. Treat that combination as a precursor."
                    ),
                    evidence=evidence_base + diagnosis.evidence[:4],
                    confidence=round(min(0.8, diagnosis.confidence), 3),
                    validation_status="OBSERVED",
                )
            )

        if executed is not None:
            worked = outcome is Outcome.SUCCESSFUL
            lessons.append(
                Lesson(
                    spacecraft_id=frame.spacecraft_id,
                    category="recovery_effectiveness",
                    description=(
                        f"'{executed.action_id}' was "
                        f"{'effective' if worked else 'not fully effective'} against "
                        f"{diagnosis.failure_mode or diagnosis.subsystem.value} "
                        f"(measured effectiveness {effectiveness:.2f})."
                    ),
                    evidence=evidence_base + [executed.rationale],
                    confidence=0.75 if worked else 0.6,
                    validation_status="VALIDATED_BY_OUTCOME",
                )
            )

        # Cross-domain co-occurrence: recorded as an association only.
        latent: list[str] = []
        if frame.cpu_load > 70.0 and frame.temperature > 40.0 and frame.state_of_charge < 60.0:
            latent.append(
                "Potential latent risk: sustained high computational workload may be associated "
                "with increased thermal stress under reduced-power conditions. Weak correlation; "
                "causality not established."
            )
            lessons.append(
                Lesson(
                    spacecraft_id=frame.spacecraft_id,
                    category="latent_risk",
                    description=latent[0],
                    evidence=evidence_base
                    + [
                        f"cpu_load {frame.cpu_load:.0f}%",
                        f"temperature {frame.temperature:.1f} C",
                        f"state_of_charge {frame.state_of_charge:.1f}%",
                    ],
                    confidence=0.45,
                    validation_status="WEAKLY_CORRELATED_UNVALIDATED",
                )
            )

        unresolved: list[str] = []
        if outcome is not Outcome.SUCCESSFUL:
            unresolved.append(
                f"{diagnosis.failure_mode or diagnosis.subsystem.value} not fully resolved by "
                f"{executed.action_id if executed else 'any executed action'}"
            )
        if diagnosis.failure_mode == "reaction_wheel_degradation":
            unresolved.append("bearing wear is not reversible; residual degradation remains")

        return MissionLearning(
            mission_id=frame.mission_id,
            lessons=lessons,
            recovery_effectiveness=outcome,
            new_limitations=(
                [f"{diagnosis.component or diagnosis.subsystem.value}: {diagnosis.probable_cause}"]
                if outcome is not Outcome.SUCCESSFUL
                else []
            ),
            unresolved_risks=unresolved,
            latent_patterns=latent,
            reasoner=ReasonerKind.DETERMINISTIC,
        )
