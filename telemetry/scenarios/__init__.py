"""Fault scenario library (master context §16).

Each scenario mutates simulator state as a function of how long the fault has
been developing. Degradations *ramp* rather than step: a fault that appears
fully formed in one frame is trivial to detect and proves nothing. The ramp is
what makes persistence and trend analysis meaningful.

`benign_thermal_transient` is deliberately not a fault. It exists to bait the
detector — an unusual-but-explainable excursion that a naive threshold flags and
ASTRIX's context scorer should suppress. Demo it alongside a real fault.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Protocol


class SimState(Protocol):
    """The subset of simulator state that scenarios are allowed to touch."""

    temp_target_offset: float
    temp_sensor_bias: float
    voltage_offset: float
    extra_load_w: float
    soc_drain_multiplier: float
    wheel_vib_offset: list[float]
    wheel_cur_offset: list[float]
    wheel_rpm_jitter: list[float]
    attitude_error_offset: float
    gyro_bias: list[float]
    cpu_offset: float
    mem_offset: float
    signal_offset: float
    packet_loss_offset: float
    latency_offset: float
    fuel_leak_rate: float


@dataclass(frozen=True)
class FaultScenario:
    key: str
    title: str
    subsystem: str
    description: str
    ramp_seconds: float
    expected_signals: tuple[str, ...]
    apply: Callable[[SimState, float, float], None]
    """apply(state, progress_0_to_1, elapsed_seconds) -> mutates state in place."""

    def progress(self, elapsed_seconds: float) -> float:
        return min(1.0, max(0.0, elapsed_seconds / self.ramp_seconds))


# --------------------------------------------------------------------------- #
# Scenario A — reaction wheel degradation (the flagship demo scenario)
# --------------------------------------------------------------------------- #

WHEEL_INDEX = 2  # zero-based -> reaction wheel #3


def _wheel_degradation(state: SimState, p: float, elapsed: float) -> None:
    # Mechanical degradation: bearing wear raises vibration first, then the motor
    # draws more current to hold speed, then RPM control loses stability, and only
    # then does pointing accuracy visibly suffer. That ordering is the signature
    # the Mission Learning Agent should recover as a precursor pattern.
    state.wheel_vib_offset[WHEEL_INDEX] += 3.4 * p
    state.wheel_cur_offset[WHEEL_INDEX] += 0.62 * p
    state.wheel_rpm_jitter[WHEEL_INDEX] = 420.0 * p
    state.attitude_error_offset += 0.95 * (p**1.6)
    # Extra torque authority costs power.
    state.extra_load_w += 14.0 * p


# --------------------------------------------------------------------------- #
# Scenario B — battery degradation
# --------------------------------------------------------------------------- #


def _battery_degradation(state: SimState, p: float, elapsed: float) -> None:
    state.voltage_offset -= 2.6 * p
    state.extra_load_w += 18.0 * p
    state.soc_drain_multiplier = 1.0 + 1.3 * p


# --------------------------------------------------------------------------- #
# Scenario C — thermal anomaly correlated with compute load
# --------------------------------------------------------------------------- #


def _thermal_anomaly(state: SimState, p: float, elapsed: float) -> None:
    state.cpu_offset += 46.0 * p
    state.temp_target_offset += 24.0 * p
    state.extra_load_w += 22.0 * p


# --------------------------------------------------------------------------- #
# Scenario D — communication degradation
# --------------------------------------------------------------------------- #


def _comms_degradation(state: SimState, p: float, elapsed: float) -> None:
    state.signal_offset -= 34.0 * p
    state.packet_loss_offset += 9.5 * p
    state.latency_offset += 2300.0 * p


# --------------------------------------------------------------------------- #
# Scenario E — gyro drift
# --------------------------------------------------------------------------- #


def _gyro_drift(state: SimState, p: float, elapsed: float) -> None:
    drift = 0.9 * p
    state.gyro_bias[0] += drift
    state.gyro_bias[2] += drift * 0.6
    state.attitude_error_offset += 1.7 * p


# --------------------------------------------------------------------------- #
# Scenario F — thermal sensor fault (looks like C, is not)
# --------------------------------------------------------------------------- #


def _thermal_sensor_fault(state: SimState, p: float, elapsed: float) -> None:
    # Only the primary sensor moves. The redundant sensor disagrees, which is how
    # the Diagnostic Agent should distinguish this from a real thermal fault.
    state.temp_sensor_bias += 21.0 * p


# --------------------------------------------------------------------------- #
# Scenario G — benign transient (false-alarm bait, NOT a fault)
# --------------------------------------------------------------------------- #


def _benign_thermal_transient(state: SimState, p: float, elapsed: float) -> None:
    # A short, shallow, fully sun-explained excursion with no corroborating
    # signal anywhere else. Expected ASTRIX verdict: WATCH, suppressed.
    bump = math.sin(math.pi * p)
    state.temp_target_offset += 7.5 * bump


SCENARIOS: dict[str, FaultScenario] = {
    s.key: s
    for s in (
        FaultScenario(
            key="wheel_degradation",
            title="Reaction Wheel #3 degradation",
            subsystem="ADCS",
            description=(
                "Progressive bearing wear on reaction wheel #3: rising vibration, "
                "rising motor current, RPM instability, growing pointing error."
            ),
            ramp_seconds=900.0,
            expected_signals=(
                "wheel_3_vibration",
                "wheel_3_current",
                "wheel_3_rpm",
                "attitude_error_deg",
            ),
            apply=_wheel_degradation,
        ),
        FaultScenario(
            key="battery_degradation",
            title="Battery capacity degradation",
            subsystem="POWER",
            description="Bus voltage sags under load and state of charge falls faster than planned.",
            ramp_seconds=1200.0,
            expected_signals=("battery_voltage", "battery_current", "state_of_charge"),
            apply=_battery_degradation,
        ),
        FaultScenario(
            key="thermal_anomaly",
            title="Thermal anomaly under compute load",
            subsystem="THERMAL",
            description="Bus temperature climbs in correlation with sustained high CPU load.",
            ramp_seconds=700.0,
            expected_signals=("temperature", "cpu_load"),
            apply=_thermal_anomaly,
        ),
        FaultScenario(
            key="comms_degradation",
            title="Communication link degradation",
            subsystem="COMMS",
            description="Signal quality falls while packet loss and downlink latency rise.",
            ramp_seconds=600.0,
            expected_signals=("communication_signal", "packet_loss", "downlink_latency_ms"),
            apply=_comms_degradation,
        ),
        FaultScenario(
            key="gyro_drift",
            title="Gyroscope bias drift",
            subsystem="ADCS",
            description="Attitude-sensor bias grows slowly, corrupting the pointing solution.",
            ramp_seconds=1500.0,
            expected_signals=("gyro_x", "gyro_z", "attitude_error_deg"),
            apply=_gyro_drift,
        ),
        FaultScenario(
            key="thermal_sensor_fault",
            title="Primary thermal sensor fault",
            subsystem="THERMAL",
            description=(
                "Primary temperature sensor reads high while the redundant sensor stays nominal — "
                "an instrumentation fault, not a thermal one."
            ),
            ramp_seconds=400.0,
            expected_signals=("temperature", "temp_sensor_delta"),
            apply=_thermal_sensor_fault,
        ),
        FaultScenario(
            key="benign_thermal_transient",
            title="Benign sun-driven thermal transient",
            subsystem="THERMAL",
            description=(
                "NOT A FAULT. Shallow sun-explained temperature excursion with no corroborating "
                "signal. Used to demonstrate false-alarm suppression."
            ),
            ramp_seconds=300.0,
            expected_signals=("temperature",),
            apply=_benign_thermal_transient,
        ),
    )
}

# Scenarios that are deliberately *not* faults. Raising an alarm on one of these is
# a false positive, which is what the dashboard and the evaluator test against.
BENIGN_SCENARIOS: frozenset[str] = frozenset({"benign_thermal_transient"})


def get_scenario(key: str) -> FaultScenario:
    if key not in SCENARIOS:
        raise KeyError(f"unknown scenario '{key}'. available: {sorted(SCENARIOS)}")
    return SCENARIOS[key]
