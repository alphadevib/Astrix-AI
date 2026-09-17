"""Custom vehicle designs must fly the same launch model honestly."""

from __future__ import annotations

import pytest

from telemetry.launch import DEFAULT_PLAN, MILESTONES, LaunchProfile
from telemetry.vehicles import PRESETS, SatelliteSpec, analyse, build_plan, generate, telemetry_factors


def fly(plan, step=2.0):
    profile = LaunchProfile(plan=plan)
    snaps = []
    while not profile.finished:
        snaps.append(profile.step(step))
    return profile, snaps


def test_default_plan_milestones_unchanged():
    assert [k for _, k, _, _ in DEFAULT_PLAN.milestones] == [k for _, k, _, _ in MILESTONES]


@pytest.mark.parametrize("key", ["astrix-reference", "cubesat-rideshare", "heavy-comsat"])
def test_feasible_presets_reach_their_target_orbit(key):
    design = PRESETS[key]
    assert analyse(design)["feasible"]
    profile, snaps = fly(build_plan(design))
    assert not profile.aborted
    assert abs(snaps[-1].altitude_km - design.target_altitude_km) < 1e-6
    assert snaps[-1].stage == len(design.rocket.stages) + 1


def test_underpowered_design_aborts_instead_of_reaching_orbit():
    design = PRESETS["underpowered-demo"]
    report = analyse(design)
    assert not report["feasible"] and report["margin_ms"] < 0
    profile, snaps = fly(build_plan(design))
    assert profile.aborted
    assert "INSUFFICIENT DELTA V" in snaps[-1].range_safety
    assert "orbit_insertion" not in [k for s in snaps for k in s.events]


def test_vehicle_that_cannot_lift_off_aborts_on_the_pad():
    design = PRESETS["astrix-reference"].model_copy(deep=True)
    design.rocket.stages[0].thrust_kn = 50.0
    profile, snaps = fly(build_plan(design))
    assert profile.aborted and snaps[-1].altitude_km == 0.0


def test_three_stage_milestones_are_ordered_and_complete():
    plan = build_plan(PRESETS["heavy-comsat"])
    _, snaps = fly(plan, step=0.7)
    events = [k for s in snaps for k in s.events]
    assert events == [k for _, k, _, _ in plan.milestones]
    assert "stage3_ignition" in events


def test_generator_parses_request():
    design = generate("3-stage rocket for a 400 kg radar satellite called SARSAT to 700 km")
    assert len(design.rocket.stages) == 3
    assert design.satellite.mass_kg == 400
    assert design.satellite.name == "SARSAT"
    assert design.target_altitude_km == 700
    assert "radar" in design.satellite.payload.lower()
    assert analyse(design)["feasible"]


def test_reference_sized_satellite_has_unit_telemetry_factors():
    factors = telemetry_factors(PRESETS["astrix-reference"].satellite)
    assert all(abs(v - 1.0) < 0.01 for v in factors.values())
    weak = telemetry_factors(SatelliteSpec(mass_kg=180, solar_w=118))
    assert weak["solar"] < 0.6
