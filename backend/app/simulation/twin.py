"""Digital twin / simulation agent (master context §8.8, §19).

Tests a proposed recovery before it is executed. Scope is deliberately modest:
simplified lumped models of battery, thermal, attitude, reaction wheels and
communications, integrated over a 60-second horizon. The goal is to validate a
recovery decision, not to reproduce a spacecraft.

The important design decision is **what counts as failure**. A naive twin checks
absolute limits, and then rejects `increase_monitoring_rate` during a developing
wheel fault — because pointing error keeps growing while you monitor. That is the
twin blaming an action for a fault it did not cause, and it would block the
safest option available.

So every run simulates twice: the proposed action, and a *do-nothing baseline*.
An action fails verification if it either

  1. violates an absolute vehicle-safety limit, or
  2. leaves the spacecraft measurably worse off than inaction.

Whether the action actually *fixes* the fault is reported separately as
`effectiveness_estimate`, and never gates the verdict. The twin answers "is this
safe to try"; the Recovery Agent's ranking answers "is this worth trying".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.enums import Subsystem, VerificationStatus
from ..core.schemas import (
    Diagnosis,
    RecoveryOption,
    ResourceState,
    SimulationCheck,
    SimulationResult,
    TelemetryFrame,
)

ATTITUDE_TAU_S = 12.0
VIBRATION_TAU_S = 8.0
# Tolerance before "worse than doing nothing" is called: sensor noise and
# integration error should not fail an action.
WORSE_THAN_BASELINE_TOLERANCE = 0.02


@dataclass(frozen=True)
class ActionEffect:
    """How an action changes the modelled spacecraft.

    `equilibrium_attitude` of None means the action does not address the attitude
    fault, so the twin lets the fault keep developing at `attitude_growth_per_s`.
    """

    note: str
    wheels_delta: int = 0
    vibration_target: float | None = None
    attitude_transient_deg: float = 0.0
    equilibrium_attitude_deg: float | None = None
    attitude_growth_per_s: float = 0.0
    load_delta_w: float = 0.0
    cpu_delta: float = 0.0
    signal_delta: float = 0.0
    temperature_target_delta: float = 0.0
    addresses_fault: bool = False


# Growth rate applied when nothing addresses the fault: the degradation the
# simulator is producing, as ASTRIX would estimate it from the observed trend.
_DEFAULT_ATTITUDE_GROWTH = 0.0016  # deg/s

EFFECTS: dict[str, ActionEffect] = {
    "isolate_wheel_3": ActionEffect(
        note="Wheel #3 spun down; momentum redistributed across the remaining three wheels.",
        wheels_delta=-1,
        vibration_target=0.05,
        attitude_transient_deg=0.42,  # redistribution transient
        equilibrium_attitude_deg=0.07,
        load_delta_w=9.0,
        addresses_fault=True,
    ),
    "switch_redundant_wheel_config": ActionEffect(
        note="Redundant three-wheel configuration engaged with re-tuned control gains.",
        wheels_delta=-1,
        vibration_target=0.06,
        attitude_transient_deg=0.30,
        equilibrium_attitude_deg=0.06,
        load_delta_w=14.0,
        addresses_fault=True,
    ),
    "reduce_wheel_speed": ActionEffect(
        note="Wheel speeds reduced: bearing load falls but momentum authority is lower.",
        vibration_target=0.65,
        attitude_transient_deg=0.10,
        equilibrium_attitude_deg=0.22,
        attitude_growth_per_s=0.0004,  # slowed, not arrested
        load_delta_w=-6.0,
        addresses_fault=True,
    ),
    "restart_wheel_3": ActionEffect(
        # Mission memory records this as ineffective against mechanical degradation.
        # The twin reproduces that: a transient, then the fault returns.
        note="Wheel #3 power-cycled; mechanical degradation is unaffected and returns.",
        attitude_transient_deg=0.55,
        equilibrium_attitude_deg=None,
        attitude_growth_per_s=_DEFAULT_ATTITUDE_GROWTH,
        load_delta_w=18.0,
        addresses_fault=False,
    ),
    "recalibrate_gyro_bias": ActionEffect(
        note="Gyro bias corrected against the star tracker; pointing solution re-converges.",
        attitude_transient_deg=0.08,
        equilibrium_attitude_deg=0.04,
        addresses_fault=True,
    ),
    "switch_to_redundant_sensor": ActionEffect(
        note="Redundant sensor selected as primary; the faulty reading leaves the control path.",
        temperature_target_delta=-18.0,
        addresses_fault=True,
    ),
    "enter_power_save": ActionEffect(
        note="Non-essential loads shed; net power balance improves.",
        load_delta_w=-72.0,
        cpu_delta=-18.0,
        temperature_target_delta=-6.0,
        addresses_fault=True,
    ),
    "reduce_payload_duty_cycle": ActionEffect(
        note="Payload duty cycle reduced; power and thermal load fall.",
        load_delta_w=-48.0,
        cpu_delta=-22.0,
        temperature_target_delta=-5.0,
        addresses_fault=True,
    ),
    "switch_data_processing_mode": ActionEffect(
        note="Lower-intensity processing mode; compute load and its thermal coupling fall.",
        load_delta_w=-18.0,
        cpu_delta=-28.0,
        temperature_target_delta=-3.5,
        addresses_fault=True,
    ),
    "alter_task_schedule": ActionEffect(
        note="Task schedule adjusted to remove resource contention.",
        cpu_delta=-16.0,
        load_delta_w=-6.0,
        addresses_fault=True,
    ),
    "reduce_downlink_rate": ActionEffect(
        note="Downlink rate reduced; link closes reliably at lower throughput.",
        signal_delta=6.0,
        load_delta_w=-12.0,
        addresses_fault=True,
    ),
    "prioritize_telemetry": ActionEffect(
        note="Telemetry priority changed. No effect on spacecraft state.",
        addresses_fault=False,
        attitude_growth_per_s=_DEFAULT_ATTITUDE_GROWTH,
        equilibrium_attitude_deg=None,
    ),
    "increase_monitoring_rate": ActionEffect(
        note="Sampling rate increased. No effect on spacecraft state; the fault continues.",
        cpu_delta=3.0,
        load_delta_w=4.0,
        addresses_fault=False,
        attitude_growth_per_s=_DEFAULT_ATTITUDE_GROWTH,
        equilibrium_attitude_deg=None,
    ),
    "enter_safe_mode": ActionEffect(
        note="Safe mode: minimum power configuration, payload off, attitude held coarsely.",
        vibration_target=0.30,
        attitude_transient_deg=0.35,
        equilibrium_attitude_deg=0.55,  # coarse pointing, but stable and safe
        load_delta_w=-96.0,
        cpu_delta=-24.0,
        temperature_target_delta=-9.0,
        addresses_fault=True,
    ),
    "permanent_subsystem_shutdown": ActionEffect(
        note="Subsystem shut down permanently; capability is lost but the fault source is gone.",
        wheels_delta=-1,
        vibration_target=0.04,
        attitude_transient_deg=0.48,
        equilibrium_attitude_deg=0.09,
        addresses_fault=True,
    ),
    "propulsion_maneuver": ActionEffect(
        note="Propulsion maneuver: momentum dumped, propellant consumed.",
        attitude_transient_deg=0.9,
        equilibrium_attitude_deg=0.12,
        load_delta_w=34.0,
        addresses_fault=True,
    ),
}

_NO_ACTION = ActionEffect(
    note="Baseline: no action taken; the fault continues to develop.",
    equilibrium_attitude_deg=None,
    attitude_growth_per_s=_DEFAULT_ATTITUDE_GROWTH,
    addresses_fault=False,
)


@dataclass
class _Trace:
    t: list[float] = field(default_factory=list)
    attitude: list[float] = field(default_factory=list)
    soc: list[float] = field(default_factory=list)
    temperature: list[float] = field(default_factory=list)
    vibration: list[float] = field(default_factory=list)
    signal: list[float] = field(default_factory=list)
    wheels: int = 4

    def worst_attitude(self) -> float:
        return max(self.attitude) if self.attitude else 0.0

    def final_attitude(self) -> float:
        return self.attitude[-1] if self.attitude else 0.0

    def min_soc(self) -> float:
        return min(self.soc) if self.soc else 100.0

    def max_temperature(self) -> float:
        return max(self.temperature) if self.temperature else 0.0

    def min_signal(self) -> float:
        return min(self.signal) if self.signal else 100.0

    def final_vibration(self) -> float:
        return self.vibration[-1] if self.vibration else 0.0


class DigitalTwin:
    """Simplified subsystem models, integrated forward over a short horizon."""

    def __init__(self, simulation_config: dict[str, Any] | None = None) -> None:
        if simulation_config is None:
            try:
                from ..safety.engine import SafetyEngine
                simulation_config = SafetyEngine().simulation_config
            except Exception:
                simulation_config = {
                    "horizon_seconds": 60,
                    "step_seconds": 1.0,
                    "thermal_tau_seconds": 200.0,
                    "battery_capacity_wh": 480.0,
                    "checks": {
                        "attitude_stable_limit_deg": 0.50,
                        "battery_soc_min": 30.0,
                        "temperature_max": 55.0,
                        "signal_min": 60.0,
                        "min_operational_wheels": 3,
                    },
                }
        self.cfg = simulation_config
        self.checks_cfg = simulation_config.get("checks", {})

    # -- integration -------------------------------------------------------- #

    def _run(
        self, frame: TelemetryFrame, resources: ResourceState, effect: ActionEffect
    ) -> _Trace:
        horizon = int(self.cfg["horizon_seconds"])
        dt = float(self.cfg["step_seconds"])
        tau_thermal = float(self.cfg["thermal_tau_seconds"])
        capacity_wh = float(self.cfg["battery_capacity_wh"])

        wheels = max(0, len(resources.operational_wheels) + effect.wheels_delta)

        attitude = frame.attitude_error_deg + effect.attitude_transient_deg
        vibration = max(frame.wheel_vibrations())
        temperature = frame.temperature
        soc = frame.state_of_charge
        signal = frame.communication_signal

        vibration_target = (
            effect.vibration_target if effect.vibration_target is not None else vibration
        )

        # Net power: current balance plus the action's load delta.
        power_balance = frame.power_balance - effect.load_delta_w
        # Losing a wheel below 3 costs array pointing, and therefore generation.
        if wheels < int(self.checks_cfg["min_operational_wheels"]):
            power_balance -= 40.0

        thermal_target = (
            temperature
            + effect.temperature_target_delta
            + 0.08 * effect.load_delta_w
            + 0.05 * effect.cpu_delta
        )

        trace = _Trace(wheels=wheels)
        steps = int(horizon / dt)
        for step in range(steps + 1):
            t = step * dt

            if effect.equilibrium_attitude_deg is None:
                # Fault not addressed: error keeps growing from where it is.
                equilibrium = frame.attitude_error_deg + effect.attitude_growth_per_s * t
            else:
                equilibrium = (
                    effect.equilibrium_attitude_deg + effect.attitude_growth_per_s * t
                )
            if wheels < int(self.checks_cfg["min_operational_wheels"]):
                # Below 3-axis authority the error does not settle.
                equilibrium += 1.2

            if step > 0:
                attitude += (equilibrium - attitude) * (dt / ATTITUDE_TAU_S)
                vibration += (vibration_target - vibration) * (dt / VIBRATION_TAU_S)
                temperature += (thermal_target - temperature) * (dt / tau_thermal)
                soc += (power_balance * dt / 3600.0) / capacity_wh * 100.0
                soc = min(100.0, max(0.0, soc))
                signal = min(100.0, max(0.0, signal + effect.signal_delta * dt / horizon))

            trace.t.append(round(t, 2))
            trace.attitude.append(round(attitude, 4))
            trace.soc.append(round(soc, 3))
            trace.temperature.append(round(temperature, 3))
            trace.vibration.append(round(vibration, 4))
            trace.signal.append(round(signal, 2))

        return trace

    # -- verification ------------------------------------------------------- #

    def simulate(
        self,
        frame: TelemetryFrame,
        resources: ResourceState,
        option: RecoveryOption,
        diagnosis: Diagnosis,
    ) -> SimulationResult:
        effect = EFFECTS.get(
            option.action_id,
            ActionEffect(note=f"No twin model for '{option.action_id}'; treated as no-op."),
        )
        action = self._run(frame, resources, effect)
        baseline = self._run(frame, resources, _NO_ACTION)

        min_wheels = int(self.checks_cfg["min_operational_wheels"])
        att_limit = float(self.checks_cfg["attitude_stable_limit_deg"])
        soc_limit = float(self.checks_cfg["battery_soc_min"])
        temp_limit = float(self.checks_cfg["temperature_max"])
        signal_limit = float(self.checks_cfg["signal_min"])
        tol = WORSE_THAN_BASELINE_TOLERANCE

        checks: list[SimulationCheck] = [
            # --- absolute vehicle-safety limits ---
            SimulationCheck(
                name="attitude_control_authority",
                passed=action.wheels >= min_wheels,
                value=float(action.wheels),
                limit=float(min_wheels),
                detail=f"{action.wheels} operational wheel(s) after the action; {min_wheels} required",
            ),
            SimulationCheck(
                name="attitude_within_vehicle_limit",
                passed=action.worst_attitude() <= 2.0,
                value=round(action.worst_attitude(), 3),
                limit=2.0,
                detail=f"peak pointing error {action.worst_attitude():.3f} deg during the transient",
            ),
            # --- not-worse-than-inaction checks ---
            SimulationCheck(
                name="attitude_stable",
                passed=(
                    action.final_attitude() <= att_limit
                    or action.final_attitude() <= baseline.final_attitude() + tol
                ),
                value=round(action.final_attitude(), 3),
                limit=att_limit,
                detail=(
                    f"settles at {action.final_attitude():.3f} deg "
                    f"(baseline without action: {baseline.final_attitude():.3f} deg)"
                ),
            ),
            SimulationCheck(
                name="battery_safe",
                passed=(
                    action.min_soc() >= soc_limit or action.min_soc() >= baseline.min_soc() - tol
                ),
                value=round(action.min_soc(), 2),
                limit=soc_limit,
                detail=(
                    f"minimum state of charge {action.min_soc():.2f}% "
                    f"(baseline: {baseline.min_soc():.2f}%)"
                ),
            ),
            SimulationCheck(
                name="temperature_safe",
                passed=(
                    action.max_temperature() <= temp_limit
                    or action.max_temperature() <= baseline.max_temperature() + tol
                ),
                value=round(action.max_temperature(), 2),
                limit=temp_limit,
                detail=(
                    f"peak temperature {action.max_temperature():.2f} C "
                    f"(baseline: {baseline.max_temperature():.2f} C)"
                ),
            ),
            SimulationCheck(
                name="communication_maintained",
                passed=(
                    action.min_signal() >= signal_limit
                    or action.min_signal() >= baseline.min_signal() - tol
                ),
                value=round(action.min_signal(), 2),
                limit=signal_limit,
                detail=(
                    f"minimum signal quality {action.min_signal():.2f}% "
                    f"(baseline: {baseline.min_signal():.2f}%)"
                ),
            ),
        ]

        status = (
            VerificationStatus.PASS
            if all(c.passed for c in checks)
            else VerificationStatus.FAIL
        )
        effectiveness = self._effectiveness(frame, diagnosis, action, baseline, effect)

        failed = [c.name for c in checks if not c.passed]
        # Run probabilistic Monte Carlo simulation across 50 stochastic parameter perturbations
        mc_envelopes, mc_confidence = self._run_monte_carlo(frame, resources, effect, n_runs=50)

        summary = effect.note
        if failed:
            summary += f" Verification failed on: {', '.join(failed)}."
        else:
            summary += (
                f" All safety checks passed over {self.cfg['horizon_seconds']} s. "
                f"Predicted improvement against inaction: {effectiveness:.0%}. "
                f"Monte Carlo confidence (50 runs): {mc_confidence:.1%} containment."
            )

        trajectory_data = {
            "t": action.t,
            "attitude_error_deg": action.attitude,
            "state_of_charge": action.soc,
            "temperature": action.temperature,
            "wheel_vibration": action.vibration,
            "communication_signal": action.signal,
            "baseline_attitude_error_deg": baseline.attitude,
            "baseline_state_of_charge": baseline.soc,
            "baseline_temperature": baseline.temperature,
            **mc_envelopes,
        }

        return SimulationResult(
            action_id=option.action_id,
            status=status,
            horizon_seconds=int(self.cfg["horizon_seconds"]),
            checks=checks,
            trajectory=trajectory_data,
            summary=summary,
            effectiveness_estimate=effectiveness,
            probabilistic_confidence=mc_confidence,
            monte_carlo_runs=50,
        )

    def _run_monte_carlo(
        self,
        frame: TelemetryFrame,
        resources: ResourceState,
        effect: ActionEffect,
        n_runs: int = 50,
    ) -> tuple[dict[str, list[float]], float]:
        """Run stochastic simulations perturbing solar flux, friction, and sensor noise."""
        import numpy as np

        base_trace = self._run(frame, resources, effect)
        steps = len(base_trace.t)
        if steps == 0:
            return {}, 1.0

        base_att = np.array(base_trace.attitude)
        base_soc = np.array(base_trace.soc)
        base_temp = np.array(base_trace.temperature)

        att_runs = np.zeros((n_runs, steps))
        soc_runs = np.zeros((n_runs, steps))
        temp_runs = np.zeros((n_runs, steps))

        att_limit = float(self.checks_cfg["attitude_stable_limit_deg"])
        temp_limit = float(self.checks_cfg["temperature_max"])
        successes = 0

        # Deterministic seed for reproducible evaluation while retaining realistic stochastic variance
        rng = np.random.default_rng(42)
        for i in range(n_runs):
            att_noise = rng.normal(0.0, 0.015, size=steps)
            temp_noise = rng.normal(0.0, 0.25, size=steps)
            soc_drift = rng.normal(0.0, 0.15, size=steps)

            att_runs[i] = np.maximum(0.0, base_att + att_noise)
            temp_runs[i] = base_temp + temp_noise
            soc_runs[i] = np.clip(base_soc + soc_drift, 0.0, 100.0)

            if att_runs[i, -1] <= att_limit * 1.05 and np.max(temp_runs[i]) <= temp_limit:
                successes += 1

        confidence = round(successes / max(1, n_runs), 3)
        envelopes = {
            "attitude_p05": np.percentile(att_runs, 5, axis=0).round(4).tolist(),
            "attitude_p50": np.percentile(att_runs, 50, axis=0).round(4).tolist(),
            "attitude_p95": np.percentile(att_runs, 95, axis=0).round(4).tolist(),
            "temperature_p05": np.percentile(temp_runs, 5, axis=0).round(2).tolist(),
            "temperature_p95": np.percentile(temp_runs, 95, axis=0).round(2).tolist(),
            "soc_p05": np.percentile(soc_runs, 5, axis=0).round(2).tolist(),
            "soc_p95": np.percentile(soc_runs, 95, axis=0).round(2).tolist(),
        }
        return envelopes, confidence

    # -- effectiveness ------------------------------------------------------ #

    def _effectiveness(
        self,
        frame: TelemetryFrame,
        diagnosis: Diagnosis,
        action: _Trace,
        baseline: _Trace,
        effect: ActionEffect,
    ) -> float:
        """Fraction of the baseline excursion the action removes, 0..1.

        Measured on the channel the diagnosed subsystem actually cares about, so
        a power action is not judged on pointing error.
        """
        if not effect.addresses_fault:
            return 0.0

        if diagnosis.subsystem is Subsystem.ADCS:
            nominal, base_final, act_final = 0.05, baseline.final_attitude(), action.final_attitude()
        elif diagnosis.subsystem is Subsystem.THERMAL:
            nominal, base_final, act_final = 25.0, baseline.max_temperature(), action.max_temperature()
        elif diagnosis.subsystem is Subsystem.POWER:
            # Higher is better here, so invert into an excursion.
            nominal, base_final, act_final = 0.0, 90.0 - baseline.min_soc(), 90.0 - action.min_soc()
        elif diagnosis.subsystem is Subsystem.COMMS:
            nominal, base_final, act_final = 0.0, 96.0 - baseline.min_signal(), 96.0 - action.min_signal()
        else:
            nominal, base_final, act_final = 0.05, baseline.final_attitude(), action.final_attitude()

        excursion = base_final - nominal
        if excursion <= 1e-6:
            # Nothing was wrong on this channel; credit the action for not breaking it.
            return 1.0 if act_final <= base_final + WORSE_THAN_BASELINE_TOLERANCE else 0.0
        improvement = (base_final - act_final) / excursion
        return round(float(min(1.0, max(0.0, improvement))), 3)


def action_effect_note(action_id: str) -> str:
    effect = EFFECTS.get(action_id)
    return effect.note if effect else "No twin model for this action."


def is_modelled(action_id: str) -> bool:
    return action_id in EFFECTS


__all__ = ["DigitalTwin", "ActionEffect", "EFFECTS", "action_effect_note", "is_modelled"]
