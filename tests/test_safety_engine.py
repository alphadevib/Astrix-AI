"""Tests for the Deterministic Safety Engine & Constraints (SR-001 through SR-011)."""

from __future__ import annotations

import pytest
from backend.app.core.enums import ApprovalStatus, Subsystem, VerificationStatus
from backend.app.core.schemas import Diagnosis, RecoveryOption, ResourceState, TelemetryFrame
from backend.app.safety.engine import SafetyEngine


@pytest.fixture
def safety_engine():
    return SafetyEngine()


def test_sr001_battery_minimum_enforced(safety_engine):
    """SR-001: Commands requiring power cannot run if SoC is below safe floor."""
    frame = TelemetryFrame(state_of_charge=12.0, battery_voltage=24.5)  # low SoC below critical floor
    resources = ResourceState(spacecraft_id="ASTRIX-01", battery_soc=12.0)
    option = RecoveryOption(
        action_id="restart_wheel_3",
        subsystem=Subsystem.ADCS,
        description="Power cycle wheel",
        rationale="Clear transient fault",
    )
    diagnosis = Diagnosis(subsystem=Subsystem.ADCS, probable_cause="wheel fault", confidence=0.9)
    verdict = safety_engine.check(frame, resources, option, diagnosis)

    # Must fail or require approval rather than auto-approving on depleted battery
    assert verdict.approval is not ApprovalStatus.AUTO_APPROVED


def test_sr005_three_wheel_authority_minimum(safety_engine):
    """SR-005: Spacecraft must preserve at least 3 operational reaction wheels."""
    # Frame where 2 wheels are already down
    frame = TelemetryFrame(wheel_1_rpm=0.0, wheel_2_rpm=0.0)
    resources = ResourceState(spacecraft_id="ASTRIX-01", operational_wheels=2)
    option = RecoveryOption(
        action_id="isolate_wheel_3",
        subsystem=Subsystem.ADCS,
        description="Isolate third wheel",
        rationale="Test wheel shutdown",
    )
    diagnosis = Diagnosis(subsystem=Subsystem.ADCS, probable_cause="wheel fault", confidence=0.9)
    verdict = safety_engine.check(frame, resources, option, diagnosis)

    # Isolating a 3rd wheel when only 2 remain violates 3-axis authority
    assert verdict.status is VerificationStatus.FAIL or verdict.approval is ApprovalStatus.BLOCKED or "SR-005" in str(verdict.violations)


def test_safe_action_approval(safety_engine):
    """A safe, low-risk telemetry prioritisation on a healthy vehicle passes."""
    frame = TelemetryFrame(
        state_of_charge=85.0,
        battery_voltage=28.2,
        temperature=25.0,
        temperature_secondary=25.0,
        attitude_error_deg=0.02,
    )
    resources = ResourceState(spacecraft_id="ASTRIX-01", battery_soc=85.0, operational_wheels=4)
    option = RecoveryOption(
        action_id="prioritize_telemetry",
        subsystem=Subsystem.COMMS,
        description="Prioritize health telemetry",
        rationale="Observe link",
    )
    diagnosis = Diagnosis(subsystem=Subsystem.COMMS, probable_cause="link margin sag", confidence=0.85)
    verdict = safety_engine.check(frame, resources, option, diagnosis)

    assert verdict.status is VerificationStatus.PASS
