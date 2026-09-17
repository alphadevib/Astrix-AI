"""Synthetic spacecraft telemetry simulator.

Stands in for the spacecraft: a physics-lite model of a LEO Earth-observation
satellite with an orbital day/night cycle, payload passes, ground contacts and
injectable faults. It is the source of both the demo telemetry and the nominal
training set for the Isolation Forest.

It is deliberately *not* the digital twin. The twin (`backend/app/simulation`)
is ASTRIX's internal model used to test recovery plans before execution; this
simulator is the ground truth ASTRIX does not get to see. Keeping them separate
is what makes the twin's verdicts meaningful rather than circular.

CLI:
    python -m telemetry.simulator --stream --scenario wheel_degradation
    python -m telemetry.simulator --nominal 4000 --out data/telemetry/nominal.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `python telemetry/simulator.py` as well as `python -m telemetry.simulator`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.core.enums import OperatingMode  # noqa: E402
from backend.app.core.schemas import TelemetryFrame  # noqa: E402
from telemetry.scenarios import SCENARIOS, FaultScenario, get_scenario  # noqa: E402

ORBIT_PERIOD_S = 5400.0  # ~90 min LEO
BATTERY_CAPACITY_WH = 480.0
BASE_LOAD_W = 118.0
PAYLOAD_LOAD_W = 92.0
COMMS_LOAD_W = 44.0
THERMAL_TAU_S = 200.0


@dataclass
class _FaultState:
    """Per-frame fault deltas. Scenarios mutate this; nominal dynamics never do."""

    temp_target_offset: float = 0.0
    temp_sensor_bias: float = 0.0
    voltage_offset: float = 0.0
    extra_load_w: float = 0.0
    soc_drain_multiplier: float = 1.0
    wheel_vib_offset: list[float] = field(default_factory=lambda: [0.0] * 4)
    wheel_cur_offset: list[float] = field(default_factory=lambda: [0.0] * 4)
    wheel_rpm_jitter: list[float] = field(default_factory=lambda: [0.0] * 4)
    attitude_error_offset: float = 0.0
    gyro_bias: list[float] = field(default_factory=lambda: [0.0] * 3)
    cpu_offset: float = 0.0
    mem_offset: float = 0.0
    signal_offset: float = 0.0
    packet_loss_offset: float = 0.0
    latency_offset: float = 0.0
    fuel_leak_rate: float = 0.0


class SpacecraftSimulator:
    def __init__(
        self,
        spacecraft_id: str = "ASTRIX-01",
        mission_id: str = "ASTRIX-M01",
        dt: float = 1.0,
        seed: int | None = 42,
        start: datetime | None = None,
        satellite: dict | None = None,
    ) -> None:
        self.spacecraft_id = spacecraft_id
        self.mission_id = mission_id
        self.dt = dt
        self.rng = random.Random(seed)
        self.t = 0.0
        self.seq = 0
        self.epoch = start or datetime.now(timezone.utc)

        # Custom satellite designs change the power budget relative to the
        # reference bus. Telemetry stays normalised to the reference bus (the
        # detector was trained on it), so a well-sized design flies reference
        # telemetry and an undersized array or battery shows up as a deficit.
        from telemetry.vehicles import telemetry_factors

        self.satellite_name = (satellite or {}).get("name") or spacecraft_id
        self.factors = telemetry_factors(satellite)

        # --- integrated state ---
        self.soc = 92.0
        self.temp = 24.0
        self.fuel = 78.0
        self.wheel_rpm = [2400.0, 2400.0, 2400.0, 2400.0]
        self.wheel_disabled = [False] * 4
        self.wheel_speed_scale = 1.0
        self.safe_mode = False
        self.power_save = False

        # --- fault injection ---
        self.scenario: FaultScenario | None = None
        self.fault_onset_t: float = 0.0

    # -- control surface ---------------------------------------------------- #

    def inject(self, scenario_key: str) -> FaultScenario:
        self.scenario = get_scenario(scenario_key)
        self.fault_onset_t = self.t
        return self.scenario

    def clear_fault(self) -> None:
        self.scenario = None

    def apply_recovery(self, action_id: str) -> str:
        """Let an approved recovery action actually change the spacecraft.

        Without this the demo's final stage is theatre: the plan is approved and
        nothing happens. With it, the operator sees vibration collapse and
        pointing re-converge, and the Mission Learning Agent has a real outcome
        to record.
        """
        if action_id == "isolate_wheel_3":
            self.wheel_disabled[2] = True
            self.clear_fault()
            return "reaction wheel #3 isolated; 3-wheel configuration active"
        if action_id == "switch_redundant_wheel_config":
            self.wheel_disabled[2] = True
            self.clear_fault()
            return "redundant attitude-control configuration engaged"
        if action_id == "reduce_wheel_speed":
            self.wheel_speed_scale = 0.62
            return "reaction wheel speeds reduced to 62%"
        if action_id == "restart_wheel_3":
            self.fault_onset_t = self.t  # clears the accumulated ramp, not the wear
            return "reaction wheel #3 restarted"
        if action_id in ("enter_power_save", "reduce_payload_duty_cycle"):
            self.power_save = True
            return "power-saving mode engaged"
        if action_id == "enter_safe_mode":
            self.safe_mode = True
            self.power_save = True
            return "safe mode entered"
        if action_id == "switch_to_redundant_sensor":
            self.clear_fault()
            return "redundant thermal sensor selected as primary"
        if action_id == "recalibrate_gyro_bias":
            self.clear_fault()
            return "gyro bias recalibrated against star tracker"
        if action_id in ("reduce_downlink_rate", "increase_monitoring_rate", "prioritize_telemetry"):
            return f"{action_id.replace('_', ' ')} applied"
        return f"no simulator effect modelled for '{action_id}'"

    # -- dynamics ----------------------------------------------------------- #

    def _orbit_phase(self) -> float:
        return (self.t % ORBIT_PERIOD_S) / ORBIT_PERIOD_S

    def _mode(self, phase: float, eclipse: bool, payload: bool, comms: bool) -> OperatingMode:
        if self.safe_mode:
            return OperatingMode.SAFE_MODE
        if 0.44 <= phase < 0.47:
            return OperatingMode.MANEUVER
        if comms:
            return OperatingMode.COMMS_PASS
        if payload:
            return OperatingMode.PAYLOAD_ACTIVE
        if eclipse:
            return OperatingMode.ECLIPSE
        return OperatingMode.NOMINAL

    def step(self) -> TelemetryFrame:
        rng = self.rng
        dt = self.dt
        phase = self._orbit_phase()

        # --- environment ---
        eclipse = phase >= 0.63
        sun_angle = 0.0 if eclipse else abs(math.sin(2 * math.pi * phase)) * 62.0
        payload = (0.05 <= phase < 0.26) and not self.safe_mode and not self.power_save
        comms = (0.30 <= phase < 0.38) and not self.safe_mode
        maneuvering = 0.44 <= phase < 0.47 and not self.safe_mode

        # --- fault deltas for this frame ---
        f = _FaultState()
        if self.scenario is not None:
            elapsed = self.t - self.fault_onset_t
            self.scenario.apply(f, self.scenario.progress(elapsed), elapsed)

        # --- power ---
        solar = 0.0 if eclipse else 236.0 * self.factors["solar"] * (0.92 + 0.08 * math.cos(2 * math.pi * phase))
        solar = max(0.0, solar + rng.gauss(0, 2.0))
        load = BASE_LOAD_W * self.factors["base_load"] + f.extra_load_w
        if payload:
            load += PAYLOAD_LOAD_W * self.factors["payload_load"]
        if comms:
            load += COMMS_LOAD_W
        if maneuvering:
            load += 30.0
        if self.safe_mode:
            load = 62.0 + f.extra_load_w
        elif self.power_save:
            load *= 0.72

        voltage = 28.1 + 0.9 * (self.soc - 80.0) / 40.0 + f.voltage_offset + rng.gauss(0, 0.04)
        voltage = max(20.0, voltage)
        net_w = solar - load
        current = net_w / voltage  # + charging, - discharging

        drain = f.soc_drain_multiplier if net_w < 0 else 1.0
        self.soc += (net_w * drain * dt / 3600.0) / (BATTERY_CAPACITY_WH * self.factors["battery"]) * 100.0
        self.soc = min(100.0, max(2.0, self.soc))

        # --- thermal (first-order lag toward a load/sun-driven target) ---
        target = 12.0 + 0.26 * sun_angle + 0.10 * load + f.temp_target_offset
        if eclipse:
            target -= 16.0
        self.temp += (target - self.temp) * (dt / THERMAL_TAU_S) + rng.gauss(0, 0.05)
        temp_secondary = self.temp - 1.1 + rng.gauss(0, 0.09)
        temp_primary = self.temp + f.temp_sensor_bias

        # --- attitude control ---
        base_rpm = 2400.0 * self.wheel_speed_scale
        rpms: list[float] = []
        vibs: list[float] = []
        currents: list[float] = []
        for i in range(4):
            if self.wheel_disabled[i]:
                rpms.append(0.0)
                vibs.append(0.05 + abs(rng.gauss(0, 0.01)))
                currents.append(0.02 + abs(rng.gauss(0, 0.005)))
                continue
            # Surviving wheels absorb the isolated wheel's momentum share.
            share = 1.0 + 0.22 * sum(self.wheel_disabled)
            target_rpm = base_rpm * share + 180.0 * math.sin(2 * math.pi * phase + i)
            if maneuvering:
                target_rpm += 620.0
            self.wheel_rpm[i] += (target_rpm - self.wheel_rpm[i]) * 0.08
            jitter = rng.gauss(0, f.wheel_rpm_jitter[i]) if f.wheel_rpm_jitter[i] > 0 else 0.0
            rpms.append(self.wheel_rpm[i] + jitter + rng.gauss(0, 6.0))
            vibs.append(max(0.02, 0.38 + 0.00004 * rpms[i] + f.wheel_vib_offset[i] + rng.gauss(0, 0.03)))
            currents.append(max(0.01, 0.52 + 0.00003 * rpms[i] + f.wheel_cur_offset[i] + rng.gauss(0, 0.012)))

        gyro = [
            f.gyro_bias[0] + rng.gauss(0, 0.004),
            f.gyro_bias[1] + rng.gauss(0, 0.004),
            f.gyro_bias[2] + rng.gauss(0, 0.004),
        ]
        if maneuvering:
            gyro = [g + 0.85 for g in gyro]

        attitude_error = 0.02 + 0.012 * max(vibs) + f.attitude_error_offset + abs(rng.gauss(0, 0.004))
        if maneuvering:
            attitude_error += 1.4
        if sum(self.wheel_disabled) >= 1:
            attitude_error += 0.06  # degraded but controllable on 3 wheels
        if sum(self.wheel_disabled) >= 2:
            attitude_error += 1.1  # below 3-axis authority

        # --- CDH ---
        cpu = 30.0 + (26.0 if payload else 0.0) + (12.0 if comms else 0.0) + f.cpu_offset
        if self.safe_mode:
            cpu = 14.0 + f.cpu_offset
        cpu = min(100.0, max(2.0, cpu + rng.gauss(0, 1.6)))
        mem = min(99.0, max(5.0, 40.0 + (14.0 if payload else 0.0) + f.mem_offset + rng.gauss(0, 1.1)))

        # --- comms ---
        signal = 96.0 if comms else 88.0
        signal = min(100.0, max(0.0, signal + f.signal_offset + rng.gauss(0, 0.8)))
        packet_loss = max(0.0, 0.1 + f.packet_loss_offset + abs(rng.gauss(0, 0.05)))
        latency = max(50.0, 620.0 + f.latency_offset + rng.gauss(0, 18.0))

        # --- propulsion ---
        if maneuvering:
            self.fuel -= 0.0009 * dt
        self.fuel -= f.fuel_leak_rate * dt
        self.fuel = max(0.0, self.fuel)

        # --- orbit geometry (circular, for display only) ---
        theta = 2 * math.pi * phase
        r = 6871.0
        position = [r * math.cos(theta), r * math.sin(theta), 0.0]
        velocity = [-7.6 * math.sin(theta), 7.6 * math.cos(theta), 0.0]

        mode = self._mode(phase, eclipse, payload, comms)
        self.seq += 1
        frame = TelemetryFrame(
            spacecraft_id=self.spacecraft_id,
            mission_id=self.mission_id,
            timestamp=self.epoch + timedelta(seconds=self.t),
            seq=self.seq,
            mode=mode,
            in_eclipse=eclipse,
            sun_angle_deg=round(sun_angle, 2),
            payload_active=payload,
            ground_contact=comms,
            battery_voltage=round(voltage, 3),
            battery_current=round(current, 3),
            state_of_charge=round(self.soc, 2),
            solar_power=round(solar, 2),
            temperature=round(temp_primary, 2),
            temperature_secondary=round(temp_secondary, 2),
            gyro_x=round(gyro[0], 5),
            gyro_y=round(gyro[1], 5),
            gyro_z=round(gyro[2], 5),
            wheel_1_rpm=round(rpms[0], 1),
            wheel_2_rpm=round(rpms[1], 1),
            wheel_3_rpm=round(rpms[2], 1),
            wheel_4_rpm=round(rpms[3], 1),
            wheel_1_vibration=round(vibs[0], 4),
            wheel_2_vibration=round(vibs[1], 4),
            wheel_3_vibration=round(vibs[2], 4),
            wheel_4_vibration=round(vibs[3], 4),
            wheel_1_current=round(currents[0], 4),
            wheel_2_current=round(currents[1], 4),
            wheel_3_current=round(currents[2], 4),
            wheel_4_current=round(currents[3], 4),
            attitude_error_deg=round(attitude_error, 4),
            thruster_status="FIRING" if maneuvering else "OFF",
            fuel_level=round(self.fuel, 3),
            cpu_load=round(cpu, 2),
            memory_usage=round(mem, 2),
            communication_signal=round(signal, 2),
            packet_loss=round(packet_loss, 3),
            downlink_latency_ms=round(latency, 1),
            position_km=[round(v, 2) for v in position],
            velocity_kms=[round(v, 4) for v in velocity],
            scenario=self.scenario.key if self.scenario else None,
            injected_fault=self.scenario.title if self.scenario else None,
        )
        self.t += dt
        return frame


# --------------------------------------------------------------------------- #
# Training-set generation
# --------------------------------------------------------------------------- #


def generate_nominal_frames(count: int, dt: float = 1.0, seed: int = 7) -> list[TelemetryFrame]:
    """Fault-free telemetry spanning several complete orbits.

    The Isolation Forest must see the full legitimate envelope — sunlit, eclipse,
    payload passes, ground contacts, slews — or it will flag every eclipse as an
    anomaly. `count` should cover at least two orbits (>= 10800 s of sim time).
    """
    sim = SpacecraftSimulator(dt=dt, seed=seed)
    # Let the thermal lag and SOC settle before recording.
    for _ in range(300):
        sim.step()
    return [sim.step() for _ in range(count)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _stream(args: argparse.Namespace) -> None:
    import httpx

    sim = SpacecraftSimulator(dt=args.dt, seed=args.seed)
    url = f"{args.api.rstrip('/')}/telemetry"
    onset = args.onset
    injected = False
    print(f"streaming to {url} (ctrl-c to stop)")
    with httpx.Client(timeout=30.0) as client:
        while True:
            if args.scenario and not injected and sim.seq >= onset:
                scenario = sim.inject(args.scenario)
                print(f"\n>>> INJECTING: {scenario.title}\n")
                injected = True
            frame = sim.step()
            payload = json.loads(frame.model_dump_json())
            try:
                response = client.post(url, json=payload)
                body = response.json() if response.content else {}
            except httpx.HTTPError as exc:
                print(f"  ! transport error: {exc}")
                time.sleep(args.interval)
                continue

            loop = body.get("loop") if isinstance(body, dict) else None
            if loop and loop.get("detection", {}).get("is_anomaly"):
                det = loop["detection"]
                flag = "SUPPRESSED" if det.get("suppressed") else det["severity"]
                print(
                    f"  seq={frame.seq:5d} {flag:<10} score={det['final_score']:.3f} "
                    f"ml={det['ml_score']:.3f}"
                )
            elif frame.seq % 20 == 0:
                print(f"  seq={frame.seq:5d} nominal  mode={frame.mode.value}")
            time.sleep(args.interval)


def _write_file(args: argparse.Namespace) -> None:
    frames = generate_nominal_frames(args.nominal, dt=args.dt, seed=args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for frame in frames:
            fh.write(frame.model_dump_json() + "\n")
    print(f"wrote {len(frames)} nominal frames to {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRIX telemetry simulator")
    parser.add_argument("--stream", action="store_true", help="POST frames to the ASTRIX API")
    parser.add_argument("--nominal", type=int, help="write N fault-free frames to --out instead")
    parser.add_argument("--out", default="data/telemetry/nominal.jsonl")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="fault to inject while streaming")
    parser.add_argument("--onset", type=int, default=60, help="frame number at which to inject")
    parser.add_argument("--interval", type=float, default=0.25, help="wall-clock seconds per frame")
    parser.add_argument("--dt", type=float, default=1.0, help="simulated seconds per frame")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--list", action="store_true", help="list fault scenarios and exit")
    args = parser.parse_args(argv)

    if args.list:
        for key, scenario in sorted(SCENARIOS.items()):
            print(f"{key:28s} [{scenario.subsystem:6s}] {scenario.description}")
        return 0
    if args.nominal:
        _write_file(args)
        return 0
    if args.stream:
        try:
            _stream(args)
        except KeyboardInterrupt:
            print("\nstopped")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
