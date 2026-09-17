"""Tests for 2D Launchpad Failure Modes and Ascent Aborts."""

from __future__ import annotations

from telemetry.launch import LaunchProfile


def test_nominal_ascent_reaches_orbit():
    profile = LaunchProfile()
    # Step through entire profile
    snap = profile.step(650.0)
    assert profile.finished is True
    assert profile.aborted is False
    assert snap.altitude_km >= 499.0
    assert snap.range_safety == "NOMINAL"


def test_premature_meco_triggers_ascent_abort():
    profile = LaunchProfile(fault="premature_meco")
    # Step past T+75s
    profile.step(85.0)

    assert profile.aborted is True
    assert profile.phase_at(profile.t) == "ASCENT_ABORT"
    snapshot = profile.snapshot()
    assert snapshot.is_abort is True
    assert "ABORT" in snapshot.range_safety


def test_max_q_excursion_detects_limit():
    profile = LaunchProfile(fault="max_q_excursion")
    # Step into max-Q zone (~45s)
    profile.step(55.0)
    snapshot = profile.snapshot()
    assert snapshot.dynamic_pressure_kpa > 40.0
    assert "dynamic pressure" in snapshot.range_safety.lower()
