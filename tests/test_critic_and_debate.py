"""Tests for the Critic Agent & Multi-Agent Debate."""

from __future__ import annotations

from backend.app.agents.critic import CriticAgent
from backend.app.core.enums import Subsystem
from backend.app.core.schemas import Diagnosis, TelemetryFrame


def test_critic_detects_sensor_instrumentation_discrepancy():
    critic = CriticAgent()
    # Primary sensor 45C, secondary 24C -> 21C delta
    frame = TelemetryFrame(temperature=45.0, temperature_secondary=24.0)
    diagnosis = Diagnosis(
        subsystem=Subsystem.THERMAL,
        probable_cause="Bus thermal excursion",
        confidence=0.8,
    )
    review = critic.review(frame, diagnosis)

    assert review.sensor_spoofing_risk == "HIGH"
    assert review.confirmation_bias_detected is True
    assert len(review.counter_arguments) > 0


def test_critic_rules_out_gyro_drift_when_wheel_vibrates():
    critic = CriticAgent()
    # High wheel 3 vibration
    frame = TelemetryFrame(wheel_3_vibration=2.8, attitude_error_deg=0.45)
    diagnosis = Diagnosis(
        subsystem=Subsystem.ADCS,
        probable_cause="Reaction wheel #3 degradation",
        confidence=0.95,
    )
    review = critic.review(frame, diagnosis)

    assert review.sensor_spoofing_risk == "LOW"
    assert review.confirmation_bias_detected is False
    assert any("Gyroscope" in h.hypothesis for h in review.ruled_out_hypotheses)
