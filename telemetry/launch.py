"""Launch and deployment profile.

A launcher carries a satellite from the pad to a circular LEO and releases it.
This is the opening act of the mission timeline:

    COUNTDOWN -> ASCENT -> ORBIT_INSERTION -> DEPLOYMENT -> (orbital simulator)

The ascent is kinematic, not a full 6-DOF trajectory: acceleration per stage is
scheduled (with a throttle bucket through max-Q) and the vehicle flies a pitch
program — vertical rise, then a smooth pitch-over to horizontal at insertion.
Altitude and downrange are integrated from speed and flight path angle, so every
quantity stays consistent (max-Q lands near T+45 s at ~33 kPa, as it should)
while remaining deterministic: the demo plays out identically every time.

Every ascent is described by a `LaunchPlan`: a list of linear-acceleration burns
plus the separation, fairing and deployment times. `DEFAULT_PLAN` is the reference
two-stage ASTRIX launcher; `telemetry.vehicles.build_plan` turns a custom rocket
design into a plan, so user-designed vehicles fly the same model. A plan whose
vehicle cannot reach orbital speed (or cannot lift off) aborts instead of
pretending to succeed.

ASTRIX's anomaly detector is trained on on-orbit telemetry, so it does not
monitor the ascent; launch health is checked against fixed range-safety limits
instead. Detection engages once the satellite is deployed and has settled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

EARTH_RADIUS_KM = 6371.0
TARGET_ALTITUDE_KM = 500.0
ORBITAL_SPEED_KMS = 7.61

COUNTDOWN_S = 10.0
MECO_S = 150.0  # stage 1 main engine cut-off
STAGE_SEP_S = 153.0
SES_S = 160.0  # stage 2 ignition
FAIRING_SEP_S = 205.0
SECO_S = 520.0  # stage 2 cut-off = orbit insertion
PAYLOAD_SEP_S = 560.0
ARRAYS_DEPLOYED_S = 590.0
DETUMBLE_COMPLETE_S = 620.0
END_S = DETUMBLE_COMPLETE_S

PREMATURE_MECO_S = 75.0

# Range-safety limits for the ascent (not learned — these are fixed vehicle limits).
MAX_AXIAL_G = 6.0
MAX_Q_KPA = 40.0

PITCH_START_S = 12.0  # vertical rise to clear the tower, then the pitch program begins
PITCH_EXPONENT = 1.65  # shape of the flight-path-angle schedule (tuned for 500 km at SECO)
THROTTLE_BUCKET = (30.0, 75.0, 70.0)  # start, end, throttle % through max-Q


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Burn:
    """One stage burn with acceleration varying linearly from `a0` to `a1`."""

    stage: int  # 1-based
    start: float
    end: float  # actual cut-off (may be before depletion if orbit is reached early)
    full_end: float  # when the stage would have run dry; drives the propellant gauge
    a0: float  # axial acceleration at ignition, m/s²
    a1: float  # axial acceleration at cut-off, m/s²
    # Fraction of the axial acceleration that becomes speed along the flight path
    # once gravity and drag losses are taken out. 1.0 for the reference vehicle,
    # whose schedule is already a net profile.
    efficiency: float = 1.0


@dataclass(frozen=True)
class LaunchPlan:
    burns: tuple[Burn, ...]
    separations: tuple[float, ...]  # jettison time of each spent stage, in order
    fairing_sep_s: float
    seco_s: float
    payload_sep_s: float
    arrays_s: float
    detumble_s: float
    target_altitude_km: float = TARGET_ALTITUDE_KM
    orbital_speed_kms: float = ORBITAL_SPEED_KMS
    throttle_bucket: tuple[float, float, float] | None = THROTTLE_BUCKET
    name: str = "ASTRIX-LV"
    payload_name: str = "ASTRIX-01"
    # Set by the design builder when the vehicle cannot make orbit.
    shortfall: bool = False
    liftoff_failure: bool = False

    @property
    def stages(self) -> int:
        return len(self.burns)

    @property
    def end_s(self) -> float:
        return self.detumble_s

    def burn_at(self, t: float) -> Burn | None:
        for burn in self.burns:
            if burn.start <= t < burn.end:
                return burn
        return None

    @property
    def milestones(self) -> tuple[tuple[float, str, str, str], ...]:
        items: list[tuple[float, str, str, str]] = [
            (-COUNTDOWN_S, "countdown", "Terminal countdown", "Launch vehicle on internal power; range is green."),
            (0.0, "liftoff", "Liftoff", "Stage 1 engines at full thrust; vehicle has cleared the tower."),
        ]
        for i, burn in enumerate(self.burns[:-1]):
            k = burn.stage
            sep = self.separations[i]
            nxt = self.burns[i + 1]
            if k == 1:
                items += [
                    (burn.end, "meco", "Main engine cut-off", "Stage 1 propellant depleted on schedule."),
                    (sep, "stage_separation", "Stage separation", "Stage 1 jettisoned."),
                ]
            else:
                items += [
                    (burn.end, f"stage{k}_cutoff", f"Stage {k} cut-off", f"Stage {k} propellant depleted."),
                    (sep, f"stage{k}_separation", f"Stage {k} separation", f"Stage {k} jettisoned."),
                ]
            upper = "Upper stage burning toward orbit." if nxt is self.burns[-1] else f"Stage {nxt.stage} burning."
            items.append((nxt.start, f"stage{nxt.stage}_ignition", f"Stage {nxt.stage} ignition", upper))
        items += [
            (self.fairing_sep_s, "fairing_separation", "Fairing separation", "Payload fairing jettisoned above the sensible atmosphere."),
            (
                self.seco_s,
                "orbit_insertion",
                "Orbit insertion",
                f"Upper-stage cut-off: {self.target_altitude_km:.0f} km circular orbit achieved.",
            ),
            (self.payload_sep_s, "payload_separation", "Payload separation", f"{self.payload_name} released from the upper stage."),
            (self.arrays_s, "arrays_deployed", "Solar arrays deployed", "Arrays latched; spacecraft is power-positive."),
            (self.detumble_s, "detumble_complete", "Detumble complete", "Reaction wheels hold attitude; handing over to nominal operations."),
        ]
        # Stable sort: a fairing jettison that precedes a staging event keeps its place.
        items = [(round(when, 1), key, title, description) for when, key, title, description in items]
        return tuple(sorted(items, key=lambda item: item[0]))

    def summary(self) -> dict:
        """Static description the 2D view needs to draw this vehicle."""
        return {
            "name": self.name,
            "payload_name": self.payload_name,
            "stages": self.stages,
            "separations": list(self.separations),
            "fairing_sep_t": self.fairing_sep_s,
            "seco_t": self.seco_s,
            "payload_sep_t": self.payload_sep_s,
            "arrays_t": self.arrays_s,
            "end_t": self.end_s,
            "target_altitude_km": self.target_altitude_km,
            "milestones": [
                {"key": key, "t": when, "title": title} for when, key, title, _ in self.milestones
            ],
        }


DEFAULT_PLAN = LaunchPlan(
    burns=(
        Burn(stage=1, start=0.0, end=MECO_S, full_end=MECO_S, a0=7.0, a1=24.0),
        # full_end leaves the 4% disposal reserve the upper stage keeps at SECO.
        Burn(stage=2, start=SES_S, end=SECO_S, full_end=535.0, a0=8.0, a1=22.4),
    ),
    separations=(STAGE_SEP_S,),
    fairing_sep_s=FAIRING_SEP_S,
    seco_s=SECO_S,
    payload_sep_s=PAYLOAD_SEP_S,
    arrays_s=ARRAYS_DEPLOYED_S,
    detumble_s=DETUMBLE_COMPLETE_S,
)

# (time from liftoff, key, title, description)
MILESTONES: tuple[tuple[float, str, str, str], ...] = DEFAULT_PLAN.milestones


@dataclass
class LaunchSnapshot:
    t: float  # seconds from liftoff (negative during countdown)
    phase: str
    stage: int  # 0 = on pad, 1..n, n+1 = payload free-flying
    altitude_km: float
    downrange_km: float
    speed_kms: float
    vertical_speed_kms: float
    flight_path_angle_deg: float
    acceleration_g: float
    dynamic_pressure_kpa: float
    throttle_pct: float
    stage1_propellant_pct: float
    stage2_propellant_pct: float
    fairing_attached: bool
    payload_attached: bool
    arrays_deployed: bool
    range_safety: str
    events: list[str] = field(default_factory=list)
    is_abort: bool = False
    fault_active: str | None = None
    stage_propellant_pct: list[float] = field(default_factory=list)
    vehicle: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


# --------------------------------------------------------------------------- #
# Kinematics
# --------------------------------------------------------------------------- #


def _acceleration(plan: LaunchPlan, t: float, fault: str | None = None) -> tuple[float, float, float]:
    """(axial m/s², net along-path m/s², throttle %) at `t` seconds after liftoff."""
    if t < 0.0 or t >= plan.seco_s:
        return 0.0, 0.0, 0.0
    if fault == "premature_meco" and t >= PREMATURE_MECO_S:
        return 0.0, 0.0, 0.0
    burn = plan.burn_at(t)
    if burn is None:
        return 0.0, 0.0, 0.0
    tau = (t - burn.start) / (burn.end - burn.start)
    accel = burn.a0 + (burn.a1 - burn.a0) * tau
    throttle = 100.0
    if burn.stage == 1:
        if plan.throttle_bucket and fault != "max_q_excursion":  # the fault is a failed bucket
            start, end, bucket = plan.throttle_bucket
            if start <= t < end:
                throttle = bucket
        accel *= throttle / 100.0
        if fault == "ascent_thrust_loss":
            accel *= 0.68  # 32% thrust shortfall
    return accel, accel * burn.efficiency, throttle


def _flight_path_angle(plan: LaunchPlan, t: float) -> float:
    """Scheduled flight path angle in radians: vertical, then a smooth pitch-over to horizontal."""
    if t < PITCH_START_S:
        return math.pi / 2
    s = min(1.0, (t - PITCH_START_S) / (plan.seco_s - PITCH_START_S))
    return math.radians(90.0 * (1.0 - s) ** PITCH_EXPONENT)


@lru_cache(maxsize=64)
def _altitude_scale(plan: LaunchPlan, dt: float = 0.5) -> float:
    # The pitch program lands within a few percent of the target; this factor
    # closes the gap so insertion altitude is exactly the target on every run.
    speed = altitude = 0.0
    t = 0.0
    while t < plan.seco_s:
        speed += _acceleration(plan, t)[1] * dt
        altitude += speed * math.sin(_flight_path_angle(plan, t)) * dt / 1000.0
        t += dt
    return plan.target_altitude_km / altitude if altitude > 0 else 1.0


def _dynamic_pressure_kpa(altitude_km: float, speed_ms: float) -> float:
    rho = 1.225 * math.exp(-altitude_km / 8.5)  # exponential atmosphere, kg/m³
    return 0.5 * rho * speed_ms**2 / 1000.0


class LaunchProfile:
    """Integrates the ascent with support for nominal flight and common ascent aborts."""

    SUB_STEP_S = 0.5

    def __init__(self, fault: str | None = None, plan: LaunchPlan | None = None) -> None:
        self.plan = plan or DEFAULT_PLAN
        self.t = -COUNTDOWN_S
        self.fault = fault
        self.aborted = False
        self.abort_t: float | None = None
        self._scale = _altitude_scale(self.plan)
        self._speed_ms = 0.0
        self._altitude_km = 0.0
        self._downrange_km = 0.0
        self._emitted: set[str] = set()

    @property
    def finished(self) -> bool:
        if self.aborted and self.abort_t is not None and self.t >= self.abort_t + 40.0:
            return True
        return self.t >= self.plan.end_s - 1e-9

    def phase_at(self, t: float) -> str:
        if self.aborted:
            return "ASCENT_ABORT"
        if t < 0.0:
            return "COUNTDOWN"
        if t < self.plan.seco_s:
            return "ASCENT"
        if t < self.plan.payload_sep_s:
            return "ORBIT_INSERTION"
        return "DEPLOYMENT"

    def inject_fault(self, fault_key: str) -> None:
        self.fault = fault_key

    def _abort_check(self, t: float, h: float) -> None:
        if self.aborted:
            return
        plan = self.plan
        if self.fault == "premature_meco" and t + h >= PREMATURE_MECO_S - 1e-9:
            self.aborted, self.abort_t = True, PREMATURE_MECO_S
        elif plan.liftoff_failure and t >= 0.0:
            self.aborted, self.abort_t = True, 0.0
            self.fault = self.fault or "insufficient_thrust"
        elif plan.shortfall and t >= plan.seco_s - 1e-9:
            self.aborted, self.abort_t = True, plan.seco_s
            self.fault = self.fault or "insufficient_delta_v"

    def step(self, dt: float) -> LaunchSnapshot:
        """Advance by `dt` seconds of mission time."""
        plan = self.plan
        target = min(plan.end_s, self.t + dt)
        while self.t < target - 1e-9:
            h = min(self.SUB_STEP_S, target - self.t)
            self._abort_check(self.t, h)
            if self.aborted:
                # Ballistic recovery arc toward splashdown
                self._speed_ms = max(180.0, self._speed_ms - 22.0 * h) if self._speed_ms > 0 else 0.0
                self._altitude_km = max(0.0, self._altitude_km - 1.2 * h)
                horizontal_ms = self._speed_ms * 0.8
            elif self.t >= plan.seco_s:
                self._speed_ms = plan.orbital_speed_kms * 1000.0
                self._altitude_km = plan.target_altitude_km
                horizontal_ms = self._speed_ms
            elif self.t >= 0.0:
                self._speed_ms += _acceleration(plan, self.t, self.fault)[1] * h
                gamma = _flight_path_angle(plan, self.t)
                self._altitude_km += self._speed_ms * math.sin(gamma) * h / 1000.0 * self._scale
                horizontal_ms = self._speed_ms * math.cos(gamma)
            else:
                horizontal_ms = 0.0

            self._downrange_km += (
                horizontal_ms * h / 1000.0 * EARTH_RADIUS_KM / (EARTH_RADIUS_KM + max(0.1, self._altitude_km))
            )
            self.t += h

        if not self.aborted and self.t >= plan.seco_s:
            self._altitude_km = plan.target_altitude_km
            self._speed_ms = plan.orbital_speed_kms * 1000.0
        return self.snapshot()

    def _propellant(self, t: float) -> list[float]:
        levels = []
        for burn in self.plan.burns:
            used = min(max(t, burn.start), burn.end) - burn.start
            levels.append(100.0 * (1.0 - used / (burn.full_end - burn.start)))
        return levels

    def snapshot(self) -> LaunchSnapshot:
        plan = self.plan
        t = self.t
        accel, _, throttle = _acceleration(plan, t, self.fault) if not self.aborted else (0.0, 0.0, 0.0)
        speed_ms = self._speed_ms
        gamma = _flight_path_angle(plan, t) if 0.0 <= t < plan.seco_s else (math.pi / 2 if t < 0 else 0.0)
        q_kpa = _dynamic_pressure_kpa(self._altitude_km, speed_ms)
        propellant = self._propellant(t)

        if t < 0.0:
            stage = 0
        elif t >= plan.payload_sep_s:
            stage = plan.stages + 1
        else:
            stage = 1 + sum(1 for sep in plan.separations if t >= sep)

        g_load = accel / 9.80665
        warnings = []
        if g_load > MAX_AXIAL_G:
            warnings.append("axial load")
        if q_kpa > MAX_Q_KPA:
            warnings.append("dynamic pressure")

        cutoff = self.abort_t if self.aborted and self.abort_t is not None else math.inf
        events = [
            key
            for when, key, _, _ in plan.milestones
            if when <= t + 1e-9 and key not in self._emitted and (when < cutoff - 0.05 or when < 0.0)
        ]
        self._emitted.update(events)

        range_safety_status = "NOMINAL"
        if self.aborted:
            range_safety_status = f"ABORT: {(self.fault or 'unknown').replace('_', ' ').upper()} — executing recovery"
        elif warnings:
            range_safety_status = "LIMIT: " + ", ".join(warnings)

        return LaunchSnapshot(
            t=t,
            phase=self.phase_at(t),
            stage=stage,
            altitude_km=self._altitude_km,
            downrange_km=self._downrange_km,
            speed_kms=speed_ms / 1000.0,
            vertical_speed_kms=(
                speed_ms * math.sin(gamma) / 1000.0
                if (t < plan.seco_s and not self.aborted)
                else (-0.8 if self.aborted else 0.0)
            ),
            flight_path_angle_deg=math.degrees(gamma) if not self.aborted else -15.0,
            acceleration_g=g_load,
            dynamic_pressure_kpa=q_kpa,
            throttle_pct=throttle,
            stage1_propellant_pct=propellant[0],
            stage2_propellant_pct=propellant[1] if len(propellant) > 1 else 0.0,
            fairing_attached=t < plan.fairing_sep_s,
            payload_attached=t < plan.payload_sep_s,
            arrays_deployed=t >= plan.arrays_s,
            range_safety=range_safety_status,
            events=events,
            is_abort=self.aborted,
            fault_active=self.fault,
            stage_propellant_pct=[round(p, 2) for p in propellant],
            vehicle={"name": plan.name, "payload_name": plan.payload_name, "stages": plan.stages},
        )


def milestone(key: str, plan: LaunchPlan | None = None) -> dict:
    for when, k, title, description in (plan or DEFAULT_PLAN).milestones:
        if k == key:
            return {"key": k, "t": when, "title": title, "description": description}
    raise KeyError(key)


def timeline(plan: LaunchPlan | None = None) -> list[dict]:
    return [milestone(k, plan) for _, k, _, _ in (plan or DEFAULT_PLAN).milestones]
