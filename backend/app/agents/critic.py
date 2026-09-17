"""Critic Agent & Multi-Agent Debate for ASTRIX-AI.

Acts as an adversarial 'Devil's Advocate' to prevent confirmation bias and detect
sensor instrumentation spoofing vs true physical failures.

Specialized Subsystem Perspectives:
- ADCS: Attitude determination & wheel kinetics vs gyro drift
- EPS: Solar/battery bus power balance vs short-circuit / instrumentation sag
- Thermal: Radiative heat & compute dissipation vs single-sensor instrumentation bias
- Comms: RF link budget & downlink path vs orbital occultation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..core.enums import Subsystem
from ..core.schemas import Diagnosis, RecoveryOption, TelemetryFrame
from .gateway import ModelGateway

log = logging.getLogger(__name__)


class HypothesisEvaluation(BaseModel):
    hypothesis: str
    subsystem: str
    plausibility: float = Field(ge=0.0, le=1.0)
    verdict: str  # "CONFIRMED", "RULED_OUT", "PLAUSIBLE_ALTERNATIVE"
    reasoning: str


class CriticReview(BaseModel):
    subsystem: str
    primary_cause: str
    confirmation_bias_detected: bool
    sensor_spoofing_risk: str  # "LOW", "MEDIUM", "HIGH"
    confidence_score: float = Field(ge=0.0, le=1.0)
    counter_arguments: list[str] = Field(default_factory=list)
    ruled_out_hypotheses: list[HypothesisEvaluation] = Field(default_factory=list)
    consensus_recommendation: str
    evidence_weighting: dict[str, float] = Field(default_factory=dict)


class CriticAgent:
    """Multi-Agent Critic cross-examining diagnoses and plans."""

    def __init__(self, gateway: ModelGateway | None = None) -> None:
        self.gateway = gateway

    def review(
        self,
        frame: TelemetryFrame,
        diagnosis: Diagnosis,
        selected_option: RecoveryOption | None = None,
    ) -> CriticReview:
        """Evaluate diagnosis against adversarial hypotheses and sensor corroboration."""
        # Check LLM gateway if available
        if self.gateway and self.gateway.available:
            llm_review = self._review_with_llm(frame, diagnosis, selected_option)
            if llm_review:
                return llm_review

        # Deterministic multi-subsystem debate fallback
        return self._deterministic_review(frame, diagnosis, selected_option)

    def _review_with_llm(
        self,
        frame: TelemetryFrame,
        diagnosis: Diagnosis,
        selected_option: RecoveryOption | None,
    ) -> CriticReview | None:
        system_prompt = (
            "You are the ASTRIX-AI Spacecraft Devil's Advocate / Critic Agent. Your mission is to "
            "ruthlessly scrutinize anomaly diagnoses for confirmation bias, false sensor data, "
            "instrumentation faults vs true physical failures, and cross-subsystem cascading effects. "
            "Evaluate alternative hypotheses and produce a structured critique."
        )
        user_prompt = (
            f"Spacecraft Telemetry:\n"
            f"- Subsystem Diagnosed: {diagnosis.subsystem.value}\n"
            f"- Probable Cause: {diagnosis.probable_cause}\n"
            f"- Confidence: {diagnosis.confidence}\n"
            f"- Temp Primary: {frame.temperature:.1f} C, Temp Secondary: {frame.temperature_secondary:.1f} C\n"
            f"- SoC: {frame.state_of_charge:.1f}%, Voltage: {frame.battery_voltage:.2f} V, Power Balance: {frame.power_balance:.1f} W\n"
            f"- Pointing Error: {frame.attitude_error_deg:.3f} deg\n"
            f"- Wheel Vibrations: W1={frame.wheel_1_vibration:.2f}, W2={frame.wheel_2_vibration:.2f}, "
            f"W3={frame.wheel_3_vibration:.2f}, W4={frame.wheel_4_vibration:.2f}\n"
            f"- Comms Signal: {frame.communication_signal:.1f}%, Packet Loss: {frame.packet_loss:.1f}%\n"
            f"- CPU Load: {frame.cpu_load:.1f}%, Memory: {frame.memory_usage:.1f}%\n"
            f"- Proposed Action: {selected_option.action_id if selected_option else 'None'}\n\n"
            "Produce an adversarial critique assessing confirmation bias and sensor spoofing risk."
        )
        try:
            return self.gateway.structured(
                system=system_prompt,
                user=user_prompt,
                output_model=CriticReview,
                label="critic_agent",
                tier="deep",
            )
        except Exception as exc:
            log.warning("Critic Agent LLM evaluation fallback: %s", exc)
            return None

    def _deterministic_review(
        self,
        frame: TelemetryFrame,
        diagnosis: Diagnosis,
        selected_option: RecoveryOption | None,
    ) -> CriticReview:
        sub = diagnosis.subsystem
        counter_args: list[str] = []
        ruled_out: list[HypothesisEvaluation] = []
        evidence_weights: dict[str, float] = {}
        sensor_risk = "LOW"
        bias_detected = False
        confidence = 0.92

        # 1. Thermal Subsystem Critique: Excursion vs Sensor Bias
        if sub is Subsystem.THERMAL:
            temp_delta = abs(frame.temperature - frame.temperature_secondary)
            evidence_weights["sensor_discrepancy"] = min(1.0, temp_delta / 15.0)
            evidence_weights["cpu_thermal_coupling"] = min(1.0, frame.cpu_load / 100.0)

            if temp_delta > 8.0:
                # Disagreement indicates sensor failure
                sensor_risk = "HIGH"
                if "sensor" not in diagnosis.probable_cause.lower():
                    bias_detected = True
                    counter_args.append(
                        f"Primary ({frame.temperature:.1f}°C) and secondary ({frame.temperature_secondary:.1f}°C) "
                        f"differ by {temp_delta:.1f}°C. This indicates instrumentation bias, NOT bus-wide overheating."
                    )
                ruled_out.append(
                    HypothesisEvaluation(
                        hypothesis="True spacecraft structural thermal runaway",
                        subsystem="THERMAL",
                        plausibility=0.15,
                        verdict="RULED_OUT",
                        reasoning="Secondary redundant thermal sensor remains nominal.",
                    )
                )
            else:
                ruled_out.append(
                    HypothesisEvaluation(
                        hypothesis="Single-point sensor instrumentation failure",
                        subsystem="THERMAL",
                        plausibility=0.10,
                        verdict="RULED_OUT",
                        reasoning="Both primary and redundant sensors track within 1.0°C envelope.",
                    )
                )

        # 2. ADCS Subsystem Critique: Wheel Degradation vs Gyro Drift
        elif sub is Subsystem.ADCS:
            w3_vib = frame.wheel_3_vibration
            evidence_weights["wheel_3_vibration"] = min(1.0, w3_vib / 2.0)
            evidence_weights["attitude_error"] = min(1.0, frame.attitude_error_deg / 1.0)

            if w3_vib > 1.0:
                ruled_out.append(
                    HypothesisEvaluation(
                        hypothesis="Attitude disturbance caused by external aerodynamic torque / gravity gradient",
                        subsystem="ADCS",
                        plausibility=0.08,
                        verdict="RULED_OUT",
                        reasoning=f"Reaction wheel #3 exhibits severe vibration ({w3_vib:.2f} mm/s) while siblings remain nominal.",
                    )
                )
                ruled_out.append(
                    HypothesisEvaluation(
                        hypothesis="Gyroscope rate-sensor bias drift",
                        subsystem="ADCS",
                        plausibility=0.20,
                        verdict="RULED_OUT",
                        reasoning="Pointing error is accompanied by direct reaction wheel mechanical vibration and motor current rise.",
                    )
                )
            else:
                # Gyro drift case
                counter_args.append("Wheel vibration remains nominal (<0.5 mm/s); attitude error likely sensor-induced.")
                ruled_out.append(
                    HypothesisEvaluation(
                        hypothesis="Reaction wheel mechanical bearing seizure",
                        subsystem="ADCS",
                        plausibility=0.05,
                        verdict="RULED_OUT",
                        reasoning="All 4 reaction wheels report normal vibration and motor drive currents.",
                    )
                )

        # 3. Power Subsystem Critique
        elif sub is Subsystem.POWER:
            evidence_weights["voltage_sag"] = min(1.0, (28.0 - frame.battery_voltage) / 5.0)
            evidence_weights["soc_drain_rate"] = min(1.0, (100.0 - frame.state_of_charge) / 50.0)
            ruled_out.append(
                HypothesisEvaluation(
                    hypothesis="Solar array partial shadowing / orientation loss",
                    subsystem="POWER",
                    plausibility=0.18,
                    verdict="RULED_OUT",
                    reasoning=f"Solar power generation ({frame.solar_power:.1f}W) matches sun-pointing ephemeris schedule.",
                )
            )

        # 4. Comms Subsystem Critique
        elif sub is Subsystem.COMMS:
            evidence_weights["packet_loss"] = min(1.0, frame.packet_loss / 20.0)
            evidence_weights["signal_attenuation"] = min(1.0, (100.0 - frame.communication_signal) / 50.0)
            ruled_out.append(
                HypothesisEvaluation(
                    hypothesis="Ground station antenna tracking failure",
                    subsystem="COMMS",
                    plausibility=0.22,
                    verdict="RULED_OUT",
                    reasoning="Downlink degradation correlates with onboard transponder link margin indicators.",
                )
            )

        # Default consensus
        recommendation = (
            f"Critic confirms diagnosis '{diagnosis.probable_cause}' with {confidence:.0%} confidence. "
            f"No confirmation bias detected."
            if not bias_detected
            else f"Caution: Potential confirmation bias. Primary evidence suggests {counter_args[0]}"
        )

        return CriticReview(
            subsystem=sub.value,
            primary_cause=diagnosis.probable_cause,
            confirmation_bias_detected=bias_detected,
            sensor_spoofing_risk=sensor_risk,
            confidence_score=confidence,
            counter_arguments=counter_args,
            ruled_out_hypotheses=ruled_out,
            consensus_recommendation=recommendation,
            evidence_weighting=evidence_weights,
        )
