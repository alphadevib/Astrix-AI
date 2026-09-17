"""Risk Assessment Agent (master context §8.5).

Severity answers "how bad is the signal". Risk answers "what does it cost the
mission, and how long do we have". The second question is the one that decides
whether a recovery is worth its own risk.

The cascading-risk output matters most. The MAVEN case in mission memory is a
cascade: an attitude anomaly drives excess power draw, which drives battery
decline, which threatens the link, which removes the ability to recover at all.
Assessing subsystems in isolation cannot see that chain.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..core.enums import OperatingMode, ReasonerKind, Severity, Subsystem
from ..core.schemas import (
    DetectionResult,
    Diagnosis,
    MemoryRecall,
    ResourceState,
    RiskAssessment,
    TelemetryFrame,
)
from .llm import AgentLLM
from .prompting import (
    ASTRIX_ROLE,
    detection_summary,
    diagnosis_summary,
    frame_summary,
    recall_summary,
    resource_summary,
)

SYSTEM = f"""{ASTRIX_ROLE}

You are the RISK ASSESSMENT AGENT. Given a diagnosis, current resources and
mission history, assess what this fault costs the mission.

Method:
1. Rate each domain (power, attitude, thermal, communication) LOW, MEDIUM or HIGH.
   Rate the domain by *consequence*, not by which subsystem is faulty — a wheel fault
   with high power draw is an attitude AND a power risk.
2. Identify cascades explicitly: state the chain, e.g. "pointing error -> higher wheel
   torque -> higher power draw -> deeper eclipse discharge -> link margin loss".
3. Estimate time to mission impact in minutes where a trend supports it, using the
   observed rate of change. Return null rather than guessing.
4. Overall mission_impact must be exactly LOW, MEDIUM or HIGH."""

_LEVELS = {"LOW", "MEDIUM", "HIGH"}


class _Draft(BaseModel):
    mission_impact: str = Field(description="LOW, MEDIUM or HIGH")
    power_risk: str = Field(description="LOW, MEDIUM or HIGH")
    attitude_risk: str = Field(description="LOW, MEDIUM or HIGH")
    thermal_risk: str = Field(description="LOW, MEDIUM or HIGH")
    communication_risk: str = Field(description="LOW, MEDIUM or HIGH")
    time_to_impact_minutes: float | None = Field(
        default=None, description="Minutes until mission impact, or null if not estimable"
    )
    cascading_risks: list[str] = Field(
        default_factory=list, description="Cross-subsystem chains, written as chains"
    )
    rationale: str = Field(description="Two or three sentences justifying the ratings")


def _level(value: str | None, default: str = "LOW") -> str:
    candidate = (value or "").strip().upper()
    return candidate if candidate in _LEVELS else default


class RiskAgent:
    name = "risk"

    def __init__(self, llm: AgentLLM) -> None:
        self.llm = llm

    def run(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        diagnosis: Diagnosis,
        resources: ResourceState,
        recall: MemoryRecall | None = None,
    ) -> RiskAssessment:
        draft = self.llm.structured(
            system=SYSTEM,
            user="\n\n".join(
                [
                    frame_summary(frame),
                    detection_summary(detection),
                    diagnosis_summary(diagnosis),
                    resource_summary(resources),
                    recall_summary(recall),
                    "Assess the mission risk.",
                ]
            ),
            output_model=_Draft,
            label=self.name,
        )
        if draft is not None:
            return RiskAssessment(
                severity=detection.severity,  # severity is the detector's call, not the LLM's
                mission_impact=_level(draft.mission_impact),
                power_risk=_level(draft.power_risk),
                attitude_risk=_level(draft.attitude_risk),
                thermal_risk=_level(draft.thermal_risk),
                communication_risk=_level(draft.communication_risk),
                time_to_impact_minutes=draft.time_to_impact_minutes,
                cascading_risks=draft.cascading_risks,
                rationale=draft.rationale,
                reasoner=ReasonerKind.LLM,
            )
        return self._deterministic(frame, detection, diagnosis, resources)

    # ------------------------------------------------------------------ #

    def _deterministic(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        diagnosis: Diagnosis,
        resources: ResourceState,
    ) -> RiskAssessment:
        power = "LOW"
        if resources.state_of_charge < 25.0:
            power = "HIGH"
        elif resources.state_of_charge < 45.0 or resources.power_margin_w < -40.0:
            power = "MEDIUM"

        attitude = "LOW"
        if not resources.attitude_control_available:
            attitude = "HIGH"
        elif frame.attitude_error_deg > 0.8 and frame.mode is not OperatingMode.MANEUVER:
            attitude = "HIGH"
        elif diagnosis.subsystem is Subsystem.ADCS:
            attitude = "MEDIUM"

        thermal = "LOW"
        if resources.thermal_headroom_c < 3.0:
            thermal = "HIGH"
        elif resources.thermal_headroom_c < 12.0:
            thermal = "MEDIUM"

        comms = "LOW"
        if not resources.link_available:
            comms = "HIGH"
        elif frame.packet_loss > 4.0 or frame.communication_signal < 70.0:
            comms = "MEDIUM"

        ranks = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        worst = max((power, attitude, thermal, comms), key=lambda level: ranks[level])
        impact = worst
        if detection.severity is Severity.CRITICAL and impact == "LOW":
            impact = "MEDIUM"

        # --- cascade detection -------------------------------------------- #
        cascades: list[str] = []
        if diagnosis.subsystem is Subsystem.ADCS and resources.power_margin_w < -10.0:
            cascades.append(
                "pointing error -> increased wheel torque demand -> higher power draw -> "
                "deeper eclipse discharge -> reduced link margin"
            )
        if thermal != "LOW" and frame.cpu_load > 75.0:
            cascades.append(
                "sustained compute load -> thermal rise -> reduced thermal headroom -> "
                "forced payload duty-cycle reduction"
            )
        if power == "HIGH" and frame.ground_contact:
            cascades.append(
                "battery decline -> transmitter power margin loss -> shortened contact window -> "
                "reduced ground recovery capability"
            )
        if not resources.attitude_control_available:
            cascades.append(
                "loss of 3-axis control -> solar array pointing loss -> power generation loss -> "
                "loss of recovery capability"
            )

        # --- time to impact from the dominant trend ----------------------- #
        time_to_impact: float | None = None
        if resources.power_margin_w < -5.0 and resources.state_of_charge > 0:
            # Minutes until the 30% floor at the current net drain.
            usable = max(0.0, resources.state_of_charge - 30.0)
            drain_per_min = abs(resources.power_margin_w) / 480.0 * 100.0 / 60.0
            if drain_per_min > 1e-6:
                time_to_impact = round(usable / drain_per_min, 1)

        return RiskAssessment(
            severity=detection.severity,
            mission_impact=impact,
            power_risk=power,
            attitude_risk=attitude,
            thermal_risk=thermal,
            communication_risk=comms,
            time_to_impact_minutes=time_to_impact,
            cascading_risks=cascades,
            rationale=(
                f"{diagnosis.subsystem.value} fault at {detection.severity.value} severity. "
                f"Worst domain risk is {worst}. State of charge {resources.state_of_charge:.1f}%, "
                f"power margin {resources.power_margin_w:+.1f} W, "
                f"{len(resources.operational_wheels)} operational wheels, "
                f"thermal headroom {resources.thermal_headroom_c:.1f} C."
            ),
            reasoner=ReasonerKind.DETERMINISTIC,
        )
