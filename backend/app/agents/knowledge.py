"""Engineering knowledge base for the reasoning agents.

Serves two purposes at once, and that is deliberate:

* It is the **deterministic fallback** reasoner. With no LLM available, matching
  telemetry against these failure modes still produces a real diagnosis and a
  real ranked recovery plan, so the demo never depends on a network call.
* It is the **grounding context** given to the LLM. The agents do not ask Claude
  to invent failure modes or action ids from nothing; they ask it to reason over
  this catalogue plus retrieved mission history. That keeps outputs inside a
  known, safety-checkable action space instead of free-form text.

The action space is closed on purpose: the Recovery Agent may only propose an
`action_id` that exists in `ACTION_CATALOG`, because only those have a modelled
effect in the digital twin and a risk tier in the safety engine. An invented
action is rejected at the boundary, not executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..core.enums import OperatingMode, RiskLevel, Subsystem
from ..core.schemas import TelemetryFrame


# --------------------------------------------------------------------------- #
# Action catalogue
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    description: str
    risk_level: RiskLevel
    subsystem: Subsystem
    expected_effect: str
    preconditions: tuple[str, ...] = ()


# Risk tiers follow master context §6:
#   GREEN  — observability and data-handling changes; no change to spacecraft state
#   YELLOW — reconfiguration: redundancy switches, workload and scheduling changes
#   RED    — mission-altering: propulsion, trajectory, permanent shutdown, safing
#
# `enter_safe_mode` is RED even though real onboard FDIR safes autonomously. In
# ASTRIX that autonomous safing belongs to the fast path (§5) — a pre-approved
# onboard response, not an LLM-planned one. Anything arriving here has been
# through deep reasoning, and halting the mission on the strength of that
# reasoning is a flight-director decision.
ACTION_CATALOG: dict[str, ActionSpec] = {
    spec.action_id: spec
    for spec in (
        # ---------------- GREEN ----------------
        ActionSpec(
            "increase_monitoring_rate",
            "Increase telemetry sampling rate on the affected subsystem",
            RiskLevel.GREEN,
            Subsystem.CDH,
            "Higher-resolution trend data; improves confidence before committing to a change",
        ),
        ActionSpec(
            "prioritize_telemetry",
            "Prioritise affected-subsystem telemetry in the downlink queue",
            RiskLevel.GREEN,
            Subsystem.COMMS,
            "Critical channels reach the ground first if the link degrades further",
        ),
        ActionSpec(
            "switch_data_processing_mode",
            "Switch to a lower-intensity onboard data-processing mode",
            RiskLevel.GREEN,
            Subsystem.CDH,
            "Reduces compute load and the thermal and power draw that follow it",
        ),
        ActionSpec(
            "reduce_downlink_rate",
            "Reduce downlink data rate for the remainder of the pass",
            RiskLevel.GREEN,
            Subsystem.COMMS,
            "Closes the link reliably at reduced throughput; no data loss",
        ),
        # ---------------- YELLOW ----------------
        ActionSpec(
            "reduce_wheel_speed",
            "Reduce reaction wheel speeds to lower bearing load",
            RiskLevel.YELLOW,
            Subsystem.ADCS,
            "Slows degradation; reduces available momentum authority",
            preconditions=("pointing budget must tolerate reduced authority",),
        ),
        ActionSpec(
            "restart_wheel_3",
            "Power-cycle reaction wheel #3",
            RiskLevel.YELLOW,
            Subsystem.ADCS,
            "Clears a control-loop latch; ineffective against mechanical degradation",
            preconditions=("transient loss of one wheel during restart",),
        ),
        ActionSpec(
            "isolate_wheel_3",
            "Isolate reaction wheel #3 and spin it down",
            RiskLevel.YELLOW,
            Subsystem.ADCS,
            "Removes the vibration source; three-wheel control retains 3-axis authority",
            preconditions=("at least 3 healthy wheels must remain", "state of charge >= 30%"),
        ),
        ActionSpec(
            "switch_redundant_wheel_config",
            "Switch to the redundant three-wheel attitude control configuration",
            RiskLevel.YELLOW,
            Subsystem.ADCS,
            "Redistributes momentum across healthy wheels and re-tunes control gains",
            preconditions=("at least 3 healthy wheels must remain",),
        ),
        ActionSpec(
            "recalibrate_gyro_bias",
            "Recalibrate gyro bias against the star tracker",
            RiskLevel.YELLOW,
            Subsystem.ADCS,
            "Restores the pointing solution; drift is expected to recur slowly",
            preconditions=("requires a quiet attitude arc", "wheel telemetry must be nominal"),
        ),
        ActionSpec(
            "switch_to_redundant_sensor",
            "Select the redundant sensor as primary",
            RiskLevel.YELLOW,
            Subsystem.THERMAL,
            "Removes a faulty reading from the control path; loses redundancy",
        ),
        ActionSpec(
            "enter_power_save",
            "Engage power-saving mode and shed non-essential loads",
            RiskLevel.YELLOW,
            Subsystem.POWER,
            "Raises eclipse-exit state of charge; suspends payload operations",
        ),
        ActionSpec(
            "reduce_payload_duty_cycle",
            "Reduce payload duty cycle",
            RiskLevel.YELLOW,
            Subsystem.PAYLOAD,
            "Lowers power and thermal load; reduces science return",
        ),
        ActionSpec(
            "alter_task_schedule",
            "Re-schedule onboard tasks to remove resource contention",
            RiskLevel.YELLOW,
            Subsystem.CDH,
            "Relieves CPU and memory pressure; delays lower-priority work",
        ),
        # ---------------- RED ----------------
        ActionSpec(
            "enter_safe_mode",
            "Transition the spacecraft to safe mode",
            RiskLevel.RED,
            Subsystem.CDH,
            "Preserves the vehicle in a known-stable low-power state; halts the mission",
            preconditions=("mission operations suspended until ground recovery",),
        ),
        ActionSpec(
            "permanent_subsystem_shutdown",
            "Permanently shut down the affected subsystem",
            RiskLevel.RED,
            Subsystem.UNKNOWN,
            "Removes the fault source irreversibly; capability is lost for the mission",
            preconditions=("irreversible",),
        ),
        ActionSpec(
            "propulsion_maneuver",
            "Execute a propulsion maneuver",
            RiskLevel.RED,
            Subsystem.PROPULSION,
            "Changes the orbit or dumps momentum; consumes irreplaceable propellant",
            preconditions=("consumes propellant", "requires validated maneuver plan"),
        ),
    )
}


def action(action_id: str) -> ActionSpec | None:
    return ACTION_CATALOG.get(action_id)


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


@dataclass
class Match:
    matched: bool
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    component: str | None = None
    ruled_out: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FailureMode:
    key: str
    title: str
    subsystem: Subsystem
    description: str
    signals: tuple[str, ...]
    candidate_actions: tuple[str, ...]
    match: Callable[[TelemetryFrame], Match]


def _worst_wheel(frame: TelemetryFrame) -> tuple[int, float, float]:
    """(1-based wheel number, vibration, motor current) of the worst wheel."""
    vibs = frame.wheel_vibrations()
    index = max(range(4), key=lambda i: vibs[i])
    return index + 1, vibs[index], frame.wheel_currents()[index]


def _match_wheel_degradation(frame: TelemetryFrame) -> Match:
    number, vibration, current = _worst_wheel(frame)
    rpms = frame.wheel_rpms()
    spread = max(rpms) - min(rpms)

    criteria = [
        (vibration > 1.2, f"wheel #{number} vibration {vibration:.2f} mm/s (nominal < 0.8)"),
        (current > 0.92, f"wheel #{number} motor current {current:.2f} A (nominal < 0.75)"),
        (spread > 400.0, f"wheel RPM spread {spread:.0f} indicates speed-control instability"),
        (frame.attitude_error_deg > 0.3, f"pointing error {frame.attitude_error_deg:.2f}° and growing"),
    ]
    fired = [note for ok, note in criteria if ok]
    # Vibration is the necessary signal: current or RPM noise alone is electrical,
    # not mechanical, and points at a different failure mode.
    matched = vibration > 1.2 and len(fired) >= 2
    ruled_out = []
    if matched and abs(frame.temperature - frame.temperature_secondary) < 3.0:
        ruled_out.append("thermal sensor fault (redundant sensors agree)")
    if matched and frame.battery_voltage > 26.5:
        ruled_out.append("power subsystem fault (bus voltage nominal)")
    return Match(
        matched=matched,
        confidence=min(0.95, 0.45 + 0.16 * len(fired)),
        evidence=fired,
        component=f"reaction_wheel_{number}",
        ruled_out=ruled_out,
    )


def _match_gyro_drift(frame: TelemetryFrame) -> Match:
    bias = max(abs(frame.gyro_x), abs(frame.gyro_y), abs(frame.gyro_z))
    _, vibration, _ = _worst_wheel(frame)
    # Rates and pointing error are *legitimately* large while slewing or thrusting.
    # Without this guard every nominal maneuver matches "gyro bias drift".
    slewing = frame.mode is OperatingMode.MANEUVER or frame.thruster_status != "OFF"
    criteria = [
        (bias > 0.12, f"gyro bias {bias:.3f} °/s with no commanded slew"),
        (frame.attitude_error_deg > 0.25, f"pointing error {frame.attitude_error_deg:.2f}°"),
        (vibration < 1.0, "reaction wheel telemetry nominal — error is not wheel-induced"),
    ]
    fired = [note for ok, note in criteria if ok]
    matched = (
        not slewing
        and bias > 0.12
        and frame.attitude_error_deg > 0.25
        and vibration < 1.0
    )
    return Match(
        matched=matched,
        confidence=min(0.9, 0.4 + 0.17 * len(fired)),
        evidence=fired,
        component="rate_gyro_assembly",
        ruled_out=["reaction wheel degradation (wheel vibration nominal)"] if matched else [],
    )


# Open-circuit bus voltage the battery should show at a given state of charge.
# Capacity degradation is a *departure from this curve* — the cell can no longer
# hold its rated voltage at that charge level — which appears long before the bus
# reaches any absolute low-voltage threshold.
_NOMINAL_BUS_V_AT_80_SOC = 28.1
_BUS_V_PER_SOC_POINT = 0.9 / 40.0
_SAG_TOLERANCE_V = 0.9  # covers load transients and sensor noise
_SAG_DECISIVE_V = 1.6  # no healthy battery sits this far off its charge curve


def expected_bus_voltage(state_of_charge: float) -> float:
    return _NOMINAL_BUS_V_AT_80_SOC + (state_of_charge - 80.0) * _BUS_V_PER_SOC_POINT


def _match_battery_degradation(frame: TelemetryFrame) -> Match:
    expected = expected_bus_voltage(frame.state_of_charge)
    sag = expected - frame.battery_voltage
    criteria = [
        (
            sag > _SAG_TOLERANCE_V,
            f"bus voltage {frame.battery_voltage:.2f} V is {sag:.2f} V below the "
            f"{expected:.2f} V expected at {frame.state_of_charge:.0f}% charge",
        ),
        (frame.battery_current < -11.0, f"discharge current {frame.battery_current:.2f} A"),
        (frame.state_of_charge < 50.0, f"state of charge {frame.state_of_charge:.1f}%"),
        (frame.power_balance < -25.0, f"power balance {frame.power_balance:.1f} W"),
    ]
    fired = [note for ok, note in criteria if ok]
    # The sag is the necessary signal: a deep discharge with a healthy voltage
    # curve is a power-budget problem, not a degrading battery. A small sag needs
    # corroboration, but a large one is unambiguous on its own — early in a
    # capacity fault the battery is still near full and sunlit, so discharge
    # current and state of charge have nothing to say yet.
    matched = sag > _SAG_DECISIVE_V or (sag > _SAG_TOLERANCE_V and len(fired) >= 2)
    return Match(
        matched=matched,
        confidence=min(0.92, 0.4 + 0.15 * len(fired)),
        evidence=fired,
        component="battery_assembly",
    )


def _match_thermal_load(frame: TelemetryFrame) -> Match:
    delta = abs(frame.temperature - frame.temperature_secondary)
    criteria = [
        (frame.temperature > 46.0, f"bus temperature {frame.temperature:.1f} °C"),
        (delta < 3.0, f"redundant sensor agrees within {delta:.1f} °C — the heat is real"),
        (frame.cpu_load > 75.0, f"CPU load {frame.cpu_load:.0f}% sustained"),
        (not frame.in_eclipse, "sunlit, so solar input contributes"),
    ]
    fired = [note for ok, note in criteria if ok]
    matched = frame.temperature > 46.0 and delta < 3.0 and frame.cpu_load > 70.0
    return Match(
        matched=matched,
        confidence=min(0.9, 0.38 + 0.15 * len(fired)),
        evidence=fired,
        component="bus_thermal_zone",
        ruled_out=["thermal sensor fault (redundant sensor agrees)"] if matched else [],
    )


def _match_thermal_sensor_fault(frame: TelemetryFrame) -> Match:
    delta = abs(frame.temperature - frame.temperature_secondary)
    criteria = [
        (delta > 10.0, f"primary/redundant sensor disagreement {delta:.1f} °C"),
        (frame.cpu_load < 80.0, "no compute-load explanation for the reading"),
        (frame.power_balance > -30.0, "no power-draw explanation for the reading"),
    ]
    fired = [note for ok, note in criteria if ok]
    matched = delta > 10.0
    return Match(
        matched=matched,
        confidence=min(0.93, 0.5 + 0.14 * len(fired)),
        evidence=fired,
        component="primary_thermal_sensor",
        ruled_out=["real thermal excursion (redundant sensor nominal)"] if matched else [],
    )


def _match_link_degradation(frame: TelemetryFrame) -> Match:
    criteria = [
        (frame.packet_loss > 2.0, f"packet loss {frame.packet_loss:.1f}%"),
        (frame.communication_signal < 80.0, f"signal quality {frame.communication_signal:.0f}%"),
        (frame.downlink_latency_ms > 1200.0, f"downlink latency {frame.downlink_latency_ms:.0f} ms"),
    ]
    fired = [note for ok, note in criteria if ok]
    matched = len(fired) >= 2
    return Match(
        matched=matched,
        confidence=min(0.88, 0.4 + 0.16 * len(fired)),
        evidence=fired,
        component="s_band_transponder",
    )


def _match_cdh_overload(frame: TelemetryFrame) -> Match:
    criteria = [
        (frame.cpu_load > 92.0, f"CPU load {frame.cpu_load:.0f}%"),
        (frame.memory_usage > 85.0, f"memory usage {frame.memory_usage:.0f}%"),
    ]
    fired = [note for ok, note in criteria if ok]
    matched = len(fired) == 2
    return Match(
        matched=matched,
        confidence=0.7 if matched else 0.0,
        evidence=fired,
        component="onboard_computer",
    )


def _match_propulsion_anomaly(frame: TelemetryFrame) -> Match:
    criteria = [
        (frame.fuel_level < 20.0, f"fuel level {frame.fuel_level:.1f}%"),
        (
            frame.thruster_status == "FIRING" and frame.mode.value != "MANEUVER",
            "thruster reported firing outside a planned maneuver",
        ),
    ]
    fired = [note for ok, note in criteria if ok]
    matched = bool(fired)
    return Match(
        matched=matched,
        confidence=0.65 if matched else 0.0,
        evidence=fired,
        component="propulsion_module",
    )


FAILURE_MODES: tuple[FailureMode, ...] = (
    FailureMode(
        key="reaction_wheel_degradation",
        title="Reaction wheel mechanical degradation",
        subsystem=Subsystem.ADCS,
        description=(
            "Bearing wear in a reaction wheel. Signature order: vibration rises, then motor "
            "current rises to hold speed, then RPM control destabilises, then pointing degrades."
        ),
        signals=("wheel_N_vibration", "wheel_N_current", "wheel_N_rpm", "attitude_error_deg"),
        candidate_actions=(
            "isolate_wheel_3",
            "switch_redundant_wheel_config",
            "reduce_wheel_speed",
            "restart_wheel_3",
            "increase_monitoring_rate",
            "enter_safe_mode",
        ),
        match=_match_wheel_degradation,
    ),
    FailureMode(
        key="gyro_bias_drift",
        title="Gyroscope bias drift",
        subsystem=Subsystem.ADCS,
        description=(
            "Rate-sensor bias grows, corrupting the attitude solution while the actuators "
            "themselves are healthy. Presents like wheel degradation but needs the opposite fix."
        ),
        signals=("gyro_x", "gyro_y", "gyro_z", "attitude_error_deg"),
        candidate_actions=("recalibrate_gyro_bias", "increase_monitoring_rate", "enter_safe_mode"),
        match=_match_gyro_drift,
    ),
    FailureMode(
        key="battery_capacity_degradation",
        title="Battery capacity degradation",
        subsystem=Subsystem.POWER,
        description="Usable capacity falls; bus voltage sags under load and discharge deepens.",
        signals=("battery_voltage", "battery_current", "state_of_charge", "power_balance"),
        candidate_actions=(
            "enter_power_save",
            "reduce_payload_duty_cycle",
            "reduce_downlink_rate",
            "increase_monitoring_rate",
            "enter_safe_mode",
        ),
        match=_match_battery_degradation,
    ),
    FailureMode(
        key="thermal_load_correlation",
        title="Thermal excursion driven by compute and solar load",
        subsystem=Subsystem.THERMAL,
        description="Real temperature rise corroborated by the redundant sensor and a load source.",
        signals=("temperature", "temperature_secondary", "cpu_load"),
        candidate_actions=(
            "switch_data_processing_mode",
            "reduce_payload_duty_cycle",
            "alter_task_schedule",
            "increase_monitoring_rate",
        ),
        match=_match_thermal_load,
    ),
    FailureMode(
        key="thermal_sensor_fault",
        title="Thermal sensor instrumentation fault",
        subsystem=Subsystem.THERMAL,
        description=(
            "Primary sensor disagrees with the redundant sensor and nothing else corroborates a "
            "temperature rise. An instrumentation fault, not a thermal one."
        ),
        signals=("temperature", "temperature_secondary", "temp_sensor_delta"),
        candidate_actions=("switch_to_redundant_sensor", "increase_monitoring_rate"),
        match=_match_thermal_sensor_fault,
    ),
    FailureMode(
        key="link_degradation",
        title="Communication link degradation",
        subsystem=Subsystem.COMMS,
        description="Signal quality falls while packet loss and latency rise.",
        signals=("communication_signal", "packet_loss", "downlink_latency_ms"),
        candidate_actions=("reduce_downlink_rate", "prioritize_telemetry", "increase_monitoring_rate"),
        match=_match_link_degradation,
    ),
    FailureMode(
        key="cdh_resource_contention",
        title="Onboard computer resource contention",
        subsystem=Subsystem.CDH,
        description="CPU and memory pressure together; risks task starvation and resets.",
        signals=("cpu_load", "memory_usage"),
        candidate_actions=("alter_task_schedule", "switch_data_processing_mode", "increase_monitoring_rate"),
        match=_match_cdh_overload,
    ),
    FailureMode(
        key="propulsion_anomaly",
        title="Propulsion anomaly",
        subsystem=Subsystem.PROPULSION,
        description="Propellant below plan, or thruster activity outside a planned maneuver.",
        signals=("fuel_level", "thruster_status"),
        candidate_actions=("increase_monitoring_rate", "enter_safe_mode"),
        match=_match_propulsion_anomaly,
    ),
)


def match_failure_modes(frame: TelemetryFrame) -> list[tuple[FailureMode, Match]]:
    """All matching failure modes, most confident first."""
    results = []
    for mode in FAILURE_MODES:
        result = mode.match(frame)
        if result.matched:
            results.append((mode, result))
    results.sort(key=lambda pair: pair[1].confidence, reverse=True)
    return results


def catalogue_for_prompt() -> str:
    """Compact text rendering of the knowledge base, for LLM grounding."""
    lines = ["FAILURE MODES:"]
    for mode in FAILURE_MODES:
        lines.append(
            f"- {mode.key} [{mode.subsystem.value}]: {mode.description} "
            f"Signals: {', '.join(mode.signals)}."
        )
    lines.append("")
    lines.append("PERMITTED RECOVERY ACTIONS (you may not invent others):")
    for spec in ACTION_CATALOG.values():
        pre = f" Preconditions: {'; '.join(spec.preconditions)}." if spec.preconditions else ""
        lines.append(
            f"- {spec.action_id} [{spec.risk_level.value}] {spec.description}. "
            f"Effect: {spec.expected_effect}.{pre}"
        )
    return "\n".join(lines)
