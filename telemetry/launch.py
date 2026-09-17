"""Launch and deployment profile for the ASTRIX demo spacecraft.

A two-stage launcher carries ASTRIX-01 from the pad to a 500 km circular LEO and
releases it. This is the opening act of the mission timeline:

    COUNTDOWN -> ASCENT -> ORBIT_INSERTION -> DEPLOYMENT -> (orbital simulator)

The ascent is kinematic, not a full 6-DOF trajectory: acceleration per stage is
scheduled (with a throttle bucket through max-Q) and the vehicle flies a pitch
program — vertical rise, then a smooth pitch-over to horizontal at insertion.
Altitude and downrange are integrated from speed and flight path angle, so every
quantity stays consistent (max-Q lands near T+45 s at ~33 kPa, as it should)
while remaining deterministic: the demo plays out identically every time.

ASTRIX's anomaly detector is trained on on-orbit telemetry, so it does not
monitor the ascent; launch health is checked against fixed range-safety limits
instead. Detection engages once the satellite is deployed and has settled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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

# (time from liftoff, key, title, description)
MILESTONES: tuple[tuple[float, str, str, str], ...] = (
    (-COUNTDOWN_S, "countdown", "Terminal countdown", "Launch vehicle on internal power; range is green."),
    (0.0, "liftoff", "Liftoff", "Stage 1 engines at full thrust; vehicle has cleared the tower."),
    (MECO_S, "meco", "Main engine cut-off", "Stage 1 propellant depleted on schedule."),
    (STAGE_SEP_S, "stage_separation", "Stage separation", "Stage 1 jettisoned."),
    (SES_S, "stage2_ignition", "Stage 2 ignition", "Upper stage burning toward orbit."),
    (FAIRING_SEP_S, "fairing_separation", "Fairing separation", "Payload fairing jettisoned above the sensible atmosphere."),
    (SECO_S, "orbit_insertion", "Orbit insertion", "Upper-stage cut-off: 500 km circular orbit achieved."),
    (PAYLOAD_SEP_S, "payload_separation", "Payload separation", "ASTRIX-01 released from the upper stage."),
    (ARRAYS_DEPLOYED_S, "arrays_deployed", "Solar arrays deployed", "Arrays latched; spacecraft is power-positive."),
    (DETUMBLE_COMPLETE_S, "detumble_complete", "Detumble complete", "Reaction wheels hold attitude; handing over to nominal operations."),
)

# Range-safety limits for the ascent (not learned — these are fixed vehicle limits).
MAX_AXIAL_G = 6.0
MAX_Q_KPA = 40.0


@dataclass
class LaunchSnapshot:
    t: float  # seconds from liftoff (negative during countdown)
    phase: str
    stage: int  # 0 = on pad, 1, 2, 3 = payload free-flying
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

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


PITCH_START_S = 12.0  # vertical rise to clear the tower, then the pitch program begins
PITCH_EXPONENT = 1.65  # shape of the flight-path-angle schedule (tuned for 500 km at SECO)
THROTTLE_BUCKET = (30.0, 75.0, 70.0)  # start, end, throttle % through max-Q


def _acceleration(t: float) -> tuple[float, float]:
    """(along-track acceleration m/s², throttle %) at `t` seconds after liftoff."""
    if t < 0.0 or MECO_S <= t < SES_S or t >= SECO_S:
        return 0.0, 0.0
    if t < MECO_S:
        start, end, bucket = THROTTLE_BUCKET
        throttle = bucket if start <= t < end else 100.0
        # Acceleration grows as stage 1 burns off propellant mass.
        return (7.0 + 17.0 * t / MECO_S) * throttle / 100.0, throttle
    tau = (t - SES_S) / (SECO_S - SES_S)
    return 8.0 + 14.4 * tau, 100.0


def _flight_path_angle(t: float) -> float:
    """Scheduled flight path angle in radians: vertical, then a smooth pitch-over to horizontal."""
    if t < PITCH_START_S:
        return math.pi / 2
    s = min(1.0, (t - PITCH_START_S) / (SECO_S - PITCH_START_S))
    return math.radians(90.0 * (1.0 - s) ** PITCH_EXPONENT)


def _integrate_raw_altitude(dt: float = 0.5) -> float:
    speed = altitude = 0.0
    t = 0.0
    while t < SECO_S:
        speed += _acceleration(t)[0] * dt
        altitude += speed * math.sin(_flight_path_angle(t)) * dt / 1000.0
        t += dt
    return altitude


# The pitch program lands within a few percent of the target; this constant closes
# the gap so insertion altitude is exactly TARGET_ALTITUDE_KM on every run.
_ALTITUDE_SCALE = TARGET_ALTITUDE_KM / _integrate_raw_altitude()


def _dynamic_pressure_kpa(altitude_km: float, speed_ms: float) -> float:
    rho = 1.225 * math.exp(-altitude_km / 8.5)  # exponential atmosphere, kg/m³
    return 0.5 * rho * speed_ms**2 / 1000.0


class LaunchProfile:
    """Integrates the ascent in fixed sub-steps and reports each milestone exactly once."""

    SUB_STEP_S = 0.5

    def __init__(self) -> None:
        self.t = -COUNTDOWN_S
        self._speed_ms = 0.0
        self._altitude_km = 0.0
        self._downrange_km = 0.0
        self._emitted: set[str] = set()

    @property
    def finished(self) -> bool:
        return self.t >= END_S - 1e-9

    @staticmethod
    def phase_at(t: float) -> str:
        if t < 0.0:
            return "COUNTDOWN"
        if t < SECO_S:
            return "ASCENT"
        if t < PAYLOAD_SEP_S:
            return "ORBIT_INSERTION"
        return "DEPLOYMENT"

    def step(self, dt: float) -> LaunchSnapshot:
        """Advance by `dt` seconds of mission time."""
        target = min(END_S, self.t + dt)
        while self.t < target - 1e-9:
            h = min(self.SUB_STEP_S, target - self.t)
            if self.t >= SECO_S:
                # Coasting in orbit: constant orbital speed, altitude held.
                self._speed_ms = ORBITAL_SPEED_KMS * 1000.0
                self._altitude_km = TARGET_ALTITUDE_KM
                horizontal_ms = self._speed_ms
            elif self.t >= 0.0:
                self._speed_ms += _acceleration(self.t)[0] * h
                gamma = _flight_path_angle(self.t)
                self._altitude_km += (
                    self._speed_ms * math.sin(gamma) * h / 1000.0 * _ALTITUDE_SCALE
                )
                horizontal_ms = self._speed_ms * math.cos(gamma)
            else:
                horizontal_ms = 0.0
            # Downrange is measured along the Earth's surface, not at altitude.
            self._downrange_km += (
                horizontal_ms * h / 1000.0 * EARTH_RADIUS_KM / (EARTH_RADIUS_KM + self._altitude_km)
            )
            self.t += h
        if self.t >= SECO_S:
            self._altitude_km = TARGET_ALTITUDE_KM
            self._speed_ms = ORBITAL_SPEED_KMS * 1000.0
        return self.snapshot()

    def snapshot(self) -> LaunchSnapshot:
        t = self.t
        accel, throttle = _acceleration(t)
        speed_ms = self._speed_ms
        gamma = _flight_path_angle(t) if 0.0 <= t < SECO_S else (math.pi / 2 if t < 0 else 0.0)
        q_kpa = _dynamic_pressure_kpa(self._altitude_km, speed_ms)

        stage1 = 100.0 * (1.0 - min(max(t, 0.0), MECO_S) / MECO_S)
        if t < SES_S:
            stage2 = 100.0
        elif t < SECO_S:
            stage2 = 4.0 + 96.0 * (1.0 - (t - SES_S) / (SECO_S - SES_S))
        else:
            stage2 = 4.0  # reserve retained for upper-stage disposal burn

        if t < 0.0:
            stage = 0
        elif t < STAGE_SEP_S:
            stage = 1
        elif t < PAYLOAD_SEP_S:
            stage = 2
        else:
            stage = 3

        g_load = accel / 9.80665
        warnings = []
        if g_load > MAX_AXIAL_G:
            warnings.append("axial load")
        if q_kpa > MAX_Q_KPA:
            warnings.append("dynamic pressure")

        events = [
            key for when, key, _, _ in MILESTONES if when <= t + 1e-9 and key not in self._emitted
        ]
        self._emitted.update(events)

        return LaunchSnapshot(
            t=t,
            phase=self.phase_at(t),
            stage=stage,
            altitude_km=self._altitude_km,
            downrange_km=self._downrange_km,
            speed_kms=speed_ms / 1000.0,
            vertical_speed_kms=speed_ms * math.sin(gamma) / 1000.0 if t < SECO_S else 0.0,
            flight_path_angle_deg=math.degrees(gamma),
            acceleration_g=g_load,
            dynamic_pressure_kpa=q_kpa,
            throttle_pct=throttle,
            stage1_propellant_pct=stage1,
            stage2_propellant_pct=stage2,
            fairing_attached=t < FAIRING_SEP_S,
            payload_attached=t < PAYLOAD_SEP_S,
            arrays_deployed=t >= ARRAYS_DEPLOYED_S,
            range_safety="NOMINAL" if not warnings else "LIMIT: " + ", ".join(warnings),
            events=events,
        )


def milestone(key: str) -> dict:
    for when, k, title, description in MILESTONES:
        if k == key:
            return {"key": k, "t": when, "title": title, "description": description}
    raise KeyError(key)


def timeline() -> list[dict]:
    return [milestone(k) for _, k, _, _ in MILESTONES]
