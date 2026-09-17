"""The deterministic failure-mode catalogue must classify every fault and nothing else.

These are simulator-only tests (no pipeline, no database), so they are fast and
pin the diagnostic layer independently of detection thresholds.
"""

from __future__ import annotations

import pytest

from backend.app.agents.knowledge import match_failure_modes
from telemetry.scenarios import BENIGN_SCENARIOS, SCENARIOS
from telemetry.simulator import ORBIT_PERIOD_S, SpacecraftSimulator

EXPECTED = {
    "wheel_degradation": "reaction_wheel_degradation",
    "gyro_drift": "gyro_bias_drift",
    "battery_degradation": "battery_capacity_degradation",
    "thermal_anomaly": "thermal_load_correlation",
    "thermal_sensor_fault": "thermal_sensor_fault",
    "comms_degradation": "link_degradation",
}


def first_match(key: str, frames: int = 1500):
    sim = SpacecraftSimulator(seed=42)
    for _ in range(30):
        sim.step()
    sim.inject(key)
    for n in range(1, frames + 1):
        matches = match_failure_modes(sim.step())
        if matches:
            return n, matches[0][0].key
    return None, None


def test_healthy_flight_matches_no_failure_mode():
    """Covers a full orbit: eclipse, imaging, ground contact and a slew.

    The slew matters most — rates and pointing error are legitimately large while
    maneuvering, and a gyro-drift rule that ignores mode fires on every one.
    """
    sim = SpacecraftSimulator(seed=42)
    modes = set()
    false_matches = []
    for _ in range(int(ORBIT_PERIOD_S) + 200):
        frame = sim.step()
        modes.add(frame.mode.value)
        for mode, _ in match_failure_modes(frame):
            false_matches.append((frame.seq, frame.mode.value, mode.key))
    assert "MANEUVER" in modes and "ECLIPSE" in modes  # the orbit really was covered
    assert false_matches == []


@pytest.mark.parametrize("scenario,expected", sorted(EXPECTED.items()))
def test_each_fault_is_classified_correctly(scenario, expected):
    at, key = first_match(scenario)
    assert key == expected, f"{scenario} classified as {key} at +{at}s"
    assert at is not None and at < 900, f"{scenario} took +{at}s to classify"


def test_benign_transient_is_never_classified_as_a_fault():
    for key in BENIGN_SCENARIOS:
        at, mode = first_match(key)
        assert mode is None, f"benign scenario {key} matched {mode} at +{at}s"


def test_every_scenario_is_covered_by_this_test():
    assert set(EXPECTED) | set(BENIGN_SCENARIOS) == set(SCENARIOS)
