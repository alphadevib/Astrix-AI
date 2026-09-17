"""The launch profile must be physically consistent and play out identically every run."""

from __future__ import annotations

from telemetry.launch import (
    MAX_AXIAL_G,
    MAX_Q_KPA,
    MILESTONES,
    ORBITAL_SPEED_KMS,
    TARGET_ALTITUDE_KM,
    LaunchProfile,
)


def fly(step: float = 2.0) -> list:
    profile = LaunchProfile()
    snapshots = []
    while not profile.finished:
        snapshots.append(profile.step(step))
    return snapshots


def test_reaches_target_orbit():
    final = fly()[-1]
    assert abs(final.altitude_km - TARGET_ALTITUDE_KM) < 1e-6
    assert abs(final.speed_kms - ORBITAL_SPEED_KMS) < 1e-6
    assert final.stage == 3 and not final.payload_attached and final.arrays_deployed


def test_nominal_ascent_stays_inside_range_safety_limits():
    snapshots = fly()
    assert all(s.range_safety == "NOMINAL" for s in snapshots)
    assert max(s.dynamic_pressure_kpa for s in snapshots) < MAX_Q_KPA
    assert max(s.acceleration_g for s in snapshots) < MAX_AXIAL_G


def test_altitude_and_downrange_never_decrease():
    snapshots = fly()
    for before, after in zip(snapshots, snapshots[1:]):
        assert after.altitude_km >= before.altitude_km - 1e-9
        assert after.downrange_km >= before.downrange_km - 1e-9


def test_every_milestone_is_reported_exactly_once_and_in_order():
    for step in (0.7, 2.0, 20.0):  # independent of the tick size
        events = [key for s in fly(step) for key in s.events]
        assert events == [key for _, key, _, _ in MILESTONES]


def test_profile_is_deterministic():
    a, b = fly(), fly()
    assert [s.as_dict() for s in a] == [s.as_dict() for s in b]
