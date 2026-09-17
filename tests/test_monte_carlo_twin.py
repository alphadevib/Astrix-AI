"""Tests for Probabilistic Digital Twin & Monte Carlo Verification."""

from __future__ import annotations

from backend.app.core.enums import Subsystem, VerificationStatus
from backend.app.core.schemas import Diagnosis, RecoveryOption, ResourceState, TelemetryFrame
from backend.app.simulation.twin import DigitalTwin


def test_monte_carlo_probabilistic_simulation():
    twin = DigitalTwin()
    frame = TelemetryFrame(wheel_3_vibration=1.8, attitude_error_deg=0.35)
    resources = ResourceState(spacecraft_id="ASTRIX-01", operational_wheels=4)
    option = RecoveryOption(
        action_id="isolate_wheel_3",
        subsystem=Subsystem.ADCS,
        description="Isolate degraded wheel",
        rationale="Stop vibration",
    )
    diagnosis = Diagnosis(subsystem=Subsystem.ADCS, probable_cause="wheel degradation", confidence=0.9)

    res = twin.simulate(frame, resources, option, diagnosis)

    assert res.status is VerificationStatus.PASS
    assert res.monte_carlo_runs == 50
    assert 0.0 <= res.probabilistic_confidence <= 1.0
    assert "attitude_p05" in res.trajectory
    assert "attitude_p95" in res.trajectory
    assert len(res.trajectory["attitude_p95"]) == len(res.trajectory["t"])
