"""Interceptor engagement simulator: trajectory check, faults and cyber attacks.

A planar (downrange / altitude) engagement against an inbound object. Before the
interceptor launches ASTRIX flies the engagement once with a healthy vehicle and
a clean link — that run *is* the trajectory check: predicted intercept point,
time of flight, lateral-load margin and keep-out rules, all as GO / NO-GO items.
The same run becomes the reference corridor for the real flight.

The real flight can carry one injected anomaly, either a vehicle fault or an
attack on the vehicle's data links. Three views of the interceptor exist, and
the whole point is that they can disagree:

    truth      where the vehicle actually is (only the simulator knows this)
    reported   what the vehicle's own navigation says, as downlinked
    radar      an independent ground radar track of the vehicle

A health monitor that only sees `reported` and `radar` (never `truth`) runs a
set of consistency checks every sample, debounces them, and diagnoses which
anomaly best explains the pattern of checks that fired.

The model is deliberately physics-lite and deterministic for a given seed: point
masses, no aerodynamics, gravity on the inbound object only, proportional
navigation guidance. It exists to show *how anomalies look from the ground*, not
to be a flight-dynamics tool.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

G_KMS2 = 0.00981
DT = 0.05
SAMPLE_EVERY = 4  # one output sample per 0.2 s
MAX_T = 90.0

# Interceptor model.
BOOST_S = 6.0
CRUISE_SPEED_KMS = 3.0
MAX_LAT_KMS2 = 0.30  # ~30 g
NAV_GAIN = 4.0
ENDURANCE_S = 60.0
LETHAL_RADIUS_KM = 0.03

# Keep-out rules for the trajectory check.
MIN_INTERCEPT_ALT_KM = 8.0
MAX_INTERCEPT_RANGE_KM = 160.0
MIN_LOAD_MARGIN = 0.25

# Link / sensor cadence.
UPLINK_EVERY = 2  # target updates at 10 Hz
GNSS_EVERY = 4  # 5 Hz
GNSS_BLEND = 0.3

LEVELS = ("NOMINAL", "WATCH", "WARNING", "CRITICAL")
DEBOUNCE = 3


@dataclass(frozen=True)
class Fault:
    key: str
    title: str
    category: str  # PHYSICAL | CYBER
    description: str
    signature: str


FAULTS: dict[str, Fault] = {
    f.key: f
    for f in (
        Fault(
            "propulsion_degradation",
            "Propulsion degradation",
            "PHYSICAL",
            "Motor under-performs after onset; the vehicle bleeds speed instead of holding cruise.",
            "Radar speed falls below the reference profile; predicted miss grows late in flight.",
        ),
        Fault(
            "actuator_stuck",
            "Control actuator stuck",
            "PHYSICAL",
            "A control surface freezes: achieved lateral acceleration no longer follows the command.",
            "Commanded vs measured lateral acceleration diverge; trajectory leaves the corridor.",
        ),
        Fault(
            "imu_gyro_drift",
            "IMU gyro drift",
            "PHYSICAL",
            "Gyro bias grows, so the vehicle's believed heading drifts from its real heading.",
            "INS/GNSS innovation stays high at every GNSS fix while the radar track still agrees with GNSS.",
        ),
        Fault(
            "tracking_noise",
            "Degraded target track",
            "PHYSICAL",
            "Ground tracking radar quality collapses; noisy target updates reach the vehicle.",
            "Target-track jitter rises; guidance commands chatter; predicted miss fluctuates.",
        ),
        Fault(
            "gnss_spoofing",
            "GNSS spoofing",
            "CYBER",
            "A spoofer slowly drags the GNSS solution off truth, staying under the innovation gate.",
            "Downlinked navigation position diverges from the independent radar track while INS/GNSS "
            "innovation stays quiet.",
        ),
        Fault(
            "uplink_jamming",
            "Uplink jamming",
            "CYBER",
            "Target-update packets are lost; the vehicle coasts on stale, extrapolated target data.",
            "Packet loss climbs; the vehicle's target estimate drifts from the ground track.",
        ),
        Fault(
            "command_injection",
            "Forged target updates",
            "CYBER",
            "An attacker injects uplink messages that move the aim point. With link authentication "
            "on they fail the MAC check and are dropped.",
            "Auth on: MAC failures, no effect on flight. Auth off: vehicle's target estimate "
            "disagrees with the ground track and the vehicle steers to a ghost.",
        ),
        Fault(
            "telemetry_replay",
            "Telemetry replay (masking attack)",
            "CYBER",
            "Recorded healthy telemetry is replayed to the ground while the vehicle is tampered with, "
            "so the operator's screen looks nominal.",
            "Telemetry sequence numbers stop advancing; downlinked position disagrees with radar.",
        ),
    )
}


CHECKS: tuple[tuple[str, str, str], ...] = (
    ("corridor", "Trajectory corridor", "Radar track vs pre-flight predicted trajectory"),
    ("predicted_miss", "Predicted miss", "Zero-effort miss growth vs the pre-flight engagement"),
    ("nav_integrity", "Nav vs radar", "Downlinked nav position vs independent radar track"),
    ("gnss_radar", "GNSS vs radar", "Downlinked raw GNSS fix vs independent radar track"),
    ("gnss_innovation", "INS/GNSS innovation", "Size of the GNSS correction at each fix"),
    ("control_tracking", "Control tracking", "Commanded vs measured lateral acceleration"),
    ("speed", "Speed profile", "Radar speed vs pre-flight predicted speed"),
    ("link_integrity", "Link integrity", "Uplink packet loss and MAC authentication failures"),
    ("uplink_echo", "Target estimate echo", "Vehicle's target estimate vs ground target track"),
    ("track_quality", "Target track quality", "Jitter in the ground radar target track"),
    ("telemetry_freshness", "Telemetry freshness", "Downlink sequence numbers must advance"),
)

# (warning, critical) thresholds per check.
THRESHOLDS: dict[str, tuple[float, float]] = {
    "corridor": (1.0, 3.0),
    "predicted_miss": (1.0, 2.5),
    "nav_integrity": (0.3, 1.0),
    "gnss_radar": (0.3, 1.0),
    "gnss_innovation": (0.06, 0.25),
    "control_tracking": (0.04, 0.10),
    "speed": (0.15, 0.5),
    "uplink_echo": (0.6, 2.0),
    "track_quality": (0.08, 0.25),
}


@dataclass
class EngagementConfig:
    target_x_km: float = 150.0
    target_y_km: float = 70.0
    target_vx_kms: float = -2.0
    target_vy_kms: float = -0.5
    target_weave_kms2: float = 0.01
    launch_delay_s: float = 4.0
    fault: str | None = None
    onset_s: float = 12.0
    severity: float = 0.6
    link_authentication: bool = True
    seed: int = 7

    def validate(self) -> None:
        if self.fault is not None and self.fault not in FAULTS:
            raise KeyError(f"unknown intercept fault '{self.fault}'")
        if not 0.0 < self.severity <= 1.0:
            raise ValueError("severity must be in (0, 1]")
        if self.target_x_km <= 0 or self.target_y_km <= 0:
            raise ValueError("target must start downrange and above the ground")


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #


@dataclass
class _Vehicle:
    x: float = 0.0
    y: float = 0.0
    heading: float = math.pi / 2  # truth heading, radians CCW from +x
    nav_x: float = 0.0
    nav_y: float = 0.0
    nav_heading: float = math.pi / 2
    speed: float = 0.0
    a_cmd: float = 0.0
    a_meas: float = 0.0
    launched: bool = False


@dataclass
class _Flight:
    samples: list[dict] = field(default_factory=list)
    closest: tuple[float, float, float, float] | None = None  # (miss, t, x, y)
    peak_lat: float = 0.0
    end_reason: str = ""


def _target_state(cfg: EngagementConfig, t: float) -> tuple[float, float, float, float]:
    """Ballistic inbound object with a gentle weave across its track."""
    vx0, vy0 = cfg.target_vx_kms, cfg.target_vy_kms
    speed = math.hypot(vx0, vy0) or 1.0
    nx, ny = -vy0 / speed, vx0 / speed  # unit normal to the initial track
    w = 0.6
    amp = cfg.target_weave_kms2
    # Position offset from integrating amp*sin(w t) twice: amp/w^2 * (w t - sin w t)
    lat_pos = amp / (w * w) * (w * t - math.sin(w * t))
    lat_vel = amp / w * (1.0 - math.cos(w * t))
    x = cfg.target_x_km + vx0 * t + nx * lat_pos
    y = cfg.target_y_km + vy0 * t - 0.5 * G_KMS2 * t * t + ny * lat_pos
    vx = vx0 + nx * lat_vel
    vy = vy0 - G_KMS2 * t + ny * lat_vel
    return x, y, vx, vy


def _lead_heading(cfg: EngagementConfig) -> float:
    """Initial launch heading: straight at a coarse predicted intercept point."""
    best = None
    for i in range(1, 1200):
        t = cfg.launch_delay_s + i * 0.05
        tx, ty, _, _ = _target_state(cfg, t)
        reach = CRUISE_SPEED_KMS * max(0.0, t - cfg.launch_delay_s - BOOST_S / 2)
        gap = abs(math.hypot(tx, ty) - reach)
        if best is None or gap < best[0]:
            best = (gap, tx, ty)
    _, tx, ty = best
    return math.atan2(max(ty, 0.5), tx)


def _closest_in_step(rx, ry, vx, vy, dt):
    """Minimum separation over one step with linear relative motion."""
    vv = vx * vx + vy * vy
    s = 0.0 if vv < 1e-12 else max(0.0, min(dt, -(rx * vx + ry * vy) / vv))
    return math.hypot(rx + vx * s, ry + vy * s), s


def _zem(ix, iy, ivx, ivy, tx, ty, tvx, tvy) -> tuple[float, float]:
    """Zero-effort miss and time-to-go (target gravity included)."""
    rx, ry = tx - ix, ty - iy
    vx, vy = tvx - ivx, tvy - ivy
    vv = vx * vx + vy * vy
    if vv < 1e-9:
        return math.hypot(rx, ry), 0.0
    tgo = -(rx * vx + ry * vy) / vv
    if tgo <= 0:
        return math.hypot(rx, ry), 0.0
    zx = rx + vx * tgo
    zy = ry + vy * tgo - 0.5 * G_KMS2 * tgo * tgo
    return math.hypot(zx, zy), tgo


def _fly(cfg: EngagementConfig, faulty: bool) -> _Flight:
    rng = random.Random(cfg.seed)
    fault = cfg.fault if faulty else None
    sev = cfg.severity
    out = _Flight()
    veh = _Vehicle()
    launch_heading = _lead_heading(cfg)
    veh.heading = veh.nav_heading = launch_heading

    # Ground's own tracks and the vehicle's knowledge of the target.
    tgt_est = None  # (x, y, vx, vy, t_rx)
    gyro_bias = 0.0
    spoof = 0.0
    stuck_value = None
    seq = 0
    replay_buffer: list[dict] = []
    replay_idx = 0
    uplink_sent = uplink_lost = mac_fail = 0
    loss_window: list[tuple[float, bool]] = []
    mac_window: list[float] = []
    last_innov = 0.0
    last_fix = (0.0, 0.0)
    track_hist: list[tuple[float, float]] = []

    steps = int(MAX_T / DT)
    for k in range(steps + 1):
        t = k * DT
        active = fault is not None and t >= cfg.onset_s
        tx, ty, tvx, tvy = _target_state(cfg, t)

        # ---- ground radar target track (noise is the tracking_noise fault)
        track_sigma = 0.01 + (0.45 * sev if fault == "tracking_noise" and active else 0.0)
        gtx = tx + rng.gauss(0, track_sigma)
        gty = ty + rng.gauss(0, track_sigma)

        # ---- uplink of target updates to the vehicle
        if veh.launched and k % UPLINK_EVERY == 0:
            uplink_sent += 1
            lost = fault == "uplink_jamming" and active and rng.random() < 0.35 + 0.6 * sev
            loss_window.append((t, lost))
            if lost:
                uplink_lost += 1
            else:
                tgt_est = (gtx, gty, tvx, tvy, t)
            if fault == "command_injection" and active and k % (UPLINK_EVERY * 2) == 0:
                forged = (tx + 0.5 * sev, ty + 4.0 * sev, tvx, tvy, t)
                if cfg.link_authentication:
                    mac_fail += 1
                    mac_window.append(t)
                else:
                    tgt_est = forged
        if tgt_est is None:
            tgt_est = (gtx, gty, tvx, tvy, t)

        # ---- vehicle guidance, control and propulsion
        if not veh.launched and t >= cfg.launch_delay_s:
            veh.launched = True
        if veh.launched:
            ft = t - cfg.launch_delay_s
            ex = tgt_est[0] + tgt_est[2] * (t - tgt_est[4])
            ey = tgt_est[1] + tgt_est[3] * (t - tgt_est[4])
            nvx, nvy = veh.speed * math.cos(veh.nav_heading), veh.speed * math.sin(veh.nav_heading)
            rx, ry = ex - veh.nav_x, ey - veh.nav_y
            rvx, rvy = tgt_est[2] - nvx, tgt_est[3] - nvy
            r2 = max(rx * rx + ry * ry, 1e-6)
            a_cmd = 0.0
            if ft > 1.0:
                los_rate = (rx * rvy - ry * rvx) / r2
                closing = -(rx * rvx + ry * rvy) / math.sqrt(r2)
                a_cmd = NAV_GAIN * max(closing, 0.0) * los_rate
            a_cmd = max(-MAX_LAT_KMS2, min(MAX_LAT_KMS2, a_cmd))
            a_real = a_cmd
            if fault == "actuator_stuck" and active:
                if stuck_value is None:
                    stuck_value = max(-MAX_LAT_KMS2, min(MAX_LAT_KMS2, veh.a_meas + 0.08 * sev))
                a_real = stuck_value * sev + a_cmd * (1 - sev)
            veh.a_cmd, veh.a_meas = a_cmd, a_real
            if r2 > 64.0:  # end-game (last ~1.5 s, inside 8 km) saturation is expected, not a margin problem
                out.peak_lat = max(out.peak_lat, abs(a_cmd))

            if ft < BOOST_S:
                veh.speed = CRUISE_SPEED_KMS * (ft + DT) / BOOST_S
            elif fault == "propulsion_degradation" and active:
                veh.speed = max(0.6, veh.speed - 0.06 * sev * DT)
            if ft > ENDURANCE_S:
                veh.speed = max(0.0, veh.speed - 0.2 * DT)

            if active and fault in ("imu_gyro_drift", "telemetry_replay"):
                gyro_bias += math.radians(0.6 * sev) * DT
            if veh.speed > 0:
                # Nav integrates what the IMU measures, not what was commanded.
                veh.nav_heading += a_real / veh.speed * DT
            veh.heading = veh.nav_heading + gyro_bias

            px, py = veh.x, veh.y
            veh.x += veh.speed * math.cos(veh.heading) * DT
            veh.y += veh.speed * math.sin(veh.heading) * DT
            veh.nav_x += veh.speed * math.cos(veh.nav_heading) * DT
            veh.nav_y += veh.speed * math.sin(veh.nav_heading) * DT

            if k % GNSS_EVERY == 0:
                if fault == "gnss_spoofing" and active:
                    spoof += 0.12 * sev * GNSS_EVERY * DT
                gx = veh.x + rng.gauss(0, 0.004)
                gy = veh.y + rng.gauss(0, 0.004) + spoof
                last_innov = math.hypot(gx - veh.nav_x, gy - veh.nav_y)
                last_fix = (gx, gy)
                veh.nav_x += GNSS_BLEND * (gx - veh.nav_x)
                veh.nav_y += GNSS_BLEND * (gy - veh.nav_y)

            # Closest approach to the real target over this step.
            ptx, pty, _, _ = _target_state(cfg, max(0.0, t - DT))
            mvx = (tx - ptx - (veh.x - px)) / DT
            mvy = (ty - pty - (veh.y - py)) / DT
            miss, s = _closest_in_step(ptx - px, pty - py, mvx, mvy, DT)
            if out.closest is None or miss < out.closest[0]:
                out.closest = (miss, t - DT + s, px + (veh.x - px) * s / DT, py + (veh.y - py) * s / DT)

        # ---- output sample (what the ground sees, plus truth for display)
        if k % SAMPLE_EVERY == 0:
            seq += 1
            rvx_r = veh.speed * math.cos(veh.heading)
            rvy_r = veh.speed * math.sin(veh.heading)
            radar = (veh.x + rng.gauss(0, 0.01), veh.y + rng.gauss(0, 0.01))
            est_x = tgt_est[0] + tgt_est[2] * (t - tgt_est[4])
            est_y = tgt_est[1] + tgt_est[3] * (t - tgt_est[4])
            telemetry = {
                "seq": seq,
                "nav": [veh.nav_x, veh.nav_y],
                "speed": veh.speed,
                "a_cmd": veh.a_cmd,
                "a_meas": veh.a_meas,
                "innovation": last_innov,
                "gnss": list(last_fix),
                "target_est": [est_x, est_y],
            }
            if fault == "telemetry_replay" and active:
                if replay_buffer:
                    telemetry = dict(replay_buffer[replay_idx % len(replay_buffer)])
                    replay_idx += 1
            else:
                replay_buffer.append(telemetry)
                replay_buffer[:] = replay_buffer[-15:]

            track_hist.append((gtx, gty))
            track_hist[:] = track_hist[-6:]
            loss_window[:] = [(lt, lost) for lt, lost in loss_window if lt > t - 1.0]
            mac_window[:] = [mt for mt in mac_window if mt > t - 1.0]

            zem, tgo = (_zem(radar[0], radar[1], rvx_r, rvy_r, gtx, gty, tvx, tvy) if veh.launched else (None, None))
            out.samples.append(
                {
                    "t": round(t, 3),
                    "launched": veh.launched,
                    "truth": [veh.x, veh.y],
                    "radar": list(radar),
                    "reported": telemetry["nav"],
                    "reported_seq": telemetry["seq"],
                    "target": [tx, ty],
                    "target_track": [gtx, gty],
                    "target_est": telemetry["target_est"],
                    "speed": veh.speed,
                    "radar_speed": veh.speed + rng.gauss(0, 0.01),
                    "a_cmd": telemetry["a_cmd"],
                    "a_meas": telemetry["a_meas"],
                    "innovation": telemetry["innovation"],
                    "gnss": telemetry["gnss"],
                    "zem": zem,
                    "tgo": tgo,
                    "packet_loss": (sum(1 for _, lost in loss_window if lost) / len(loss_window)) if loss_window else 0.0,
                    "mac_failures": len(mac_window),
                    "track_jitter": _jitter(track_hist),
                    "fault_active": active,
                }
            )

        # ---- termination
        if veh.launched and out.closest is not None:
            miss, ct, _, _ = out.closest
            if miss <= LETHAL_RADIUS_KM:
                out.end_reason = "intercept"
                break
            if t - ct > 3.0 and miss < 50:
                out.end_reason = "passed"
                break
        if ty <= 0:
            out.end_reason = "target_impact"
            break
        if veh.launched and veh.y < 0:
            out.end_reason = "interceptor_ground"
            break
    else:
        out.end_reason = "timeout"
    return out


def _jitter(hist: list[tuple[float, float]]) -> float:
    if len(hist) < 3:
        return 0.0
    d2 = [
        math.hypot(hist[i + 1][0] - 2 * hist[i][0] + hist[i - 1][0], hist[i + 1][1] - 2 * hist[i][1] + hist[i - 1][1])
        for i in range(1, len(hist) - 1)
    ]
    return sum(d2) / len(d2)


# --------------------------------------------------------------------------- #
# Health monitor (ground side: never reads truth)
# --------------------------------------------------------------------------- #


def _level(value: float, key: str) -> str:
    warn, crit = THRESHOLDS[key]
    if value >= crit:
        return "CRITICAL"
    if value >= warn:
        return "WARNING"
    if value >= warn * 0.6:
        return "WATCH"
    return "NOMINAL"


def _monitor(samples: list[dict], nominal: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Annotate samples with debounced check levels; return events and first-trip times."""
    ref = {round(s["t"], 3): s for s in nominal}
    streak: dict[str, int] = {key: 0 for key, _, _ in CHECKS}
    pending: dict[str, str] = {key: "NOMINAL" for key, _, _ in CHECKS}
    state: dict[str, str] = {key: "NOMINAL" for key, _, _ in CHECKS}
    first: dict[str, dict] = {}
    events: list[dict] = []
    prev_seq = None
    smooth: dict[str, float] = {}

    def ema(key, value, alpha=0.35):
        smooth[key] = value if key not in smooth else smooth[key] + alpha * (value - smooth[key])
        return smooth[key]

    for s in samples:
        raw: dict[str, tuple[str, float]] = {}
        if s["launched"]:
            r = ref.get(round(s["t"], 3))
            if r is not None and r["launched"]:
                dev = math.dist(s["radar"], r["radar"])
                raw["corridor"] = (_level(dev, "corridor"), dev)
                sp = max(0.0, r["speed"] - s["radar_speed"])
                raw["speed"] = (_level(ema("speed", sp), "speed"), sp)
                if s["zem"] is not None and r["zem"] is not None and s["tgo"] and s["t"] > 8:
                    excess = max(0.0, s["zem"] - r["zem"])
                    raw["predicted_miss"] = (_level(ema("zem", excess), "predicted_miss"), excess)
            nav = math.dist(s["reported"], s["radar"])
            raw["nav_integrity"] = (_level(ema("nav", nav), "nav_integrity"), nav)
            fix = math.dist(s["gnss"], s["radar"])
            raw["gnss_radar"] = (_level(ema("fix", fix), "gnss_radar"), fix)
            raw["gnss_innovation"] = (_level(ema("innov", s["innovation"]), "gnss_innovation"), s["innovation"])
            ctl = abs(s["a_cmd"] - s["a_meas"])
            raw["control_tracking"] = (_level(ema("ctl", ctl), "control_tracking"), ctl)
            echo = math.dist(s["target_est"], s["target_track"])
            raw["uplink_echo"] = (_level(ema("echo", echo), "uplink_echo"), echo)
            loss = s["packet_loss"]
            link = "CRITICAL" if loss > 0.5 or s["mac_failures"] >= 3 else "WARNING" if loss > 0.2 or s["mac_failures"] > 0 else "NOMINAL"
            raw["link_integrity"] = (link, max(loss, s["mac_failures"]))
        raw["track_quality"] = (_level(ema("jit", s["track_jitter"]), "track_quality"), s["track_jitter"])
        fresh = "NOMINAL" if prev_seq is None or s["reported_seq"] > prev_seq else "CRITICAL"
        raw["telemetry_freshness"] = (fresh, float(s["reported_seq"]))
        prev_seq = s["reported_seq"] if prev_seq is None else max(prev_seq, s["reported_seq"])

        checks = {}
        for key, _, _ in CHECKS:
            level, value = raw.get(key, ("NOMINAL", 0.0))
            # Debounce both ways: a level must persist DEBOUNCE samples to raise or
            # lower an alert, so one noisy sample neither alarms nor clears.
            if level == pending[key]:
                streak[key] += 1
            else:
                pending[key], streak[key] = level, 1
            if level != state[key] and streak[key] >= DEBOUNCE:
                if LEVELS.index(level) >= 2 and LEVELS.index(level) > LEVELS.index(state[key]):
                    events.append({"t": s["t"], "check": key, "level": level, "value": round(value, 4)})
                    first.setdefault(key, {"t": s["t"], "level": level, "value": round(value, 4)})
                state[key] = level
            checks[key] = state[key]
        s["checks"] = checks
    return events, first


def _diagnose(first: dict[str, dict], samples: list[dict], cfg: EngagementConfig) -> list[dict]:
    """Rule-based attribution from the pattern of checks that tripped."""
    fired = set(first)
    when = lambda *keys: min((first[k]["t"] for k in keys if k in first), default=None)  # noqa: E731
    mac = any(s["mac_failures"] for s in samples)
    loss = max((s["packet_loss"] for s in samples), default=0.0) > 0.2
    out: list[dict] = []

    def add(key, confidence, evidence, actions, trigger):
        fault = FAULTS[key]
        out.append(
            {
                "cause": key,
                "title": fault.title,
                "category": fault.category,
                "confidence": confidence,
                "t": when(*trigger),
                "evidence": evidence,
                "actions": actions,
            }
        )

    if "telemetry_freshness" in fired:
        add(
            "telemetry_replay", 0.92,
            ["Downlink sequence numbers stopped advancing — telemetry is being replayed"]
            + (["Replayed nav position disagrees with independent radar track"] if "nav_integrity" in fired else []),
            ["Distrust downlinked telemetry; fly the engagement picture from radar only",
             "Rotate downlink session keys and require per-frame timestamps in the MAC",
             "Treat any concurrent vehicle behaviour as potentially adversarial"],
            ("telemetry_freshness",),
        )
    if mac and "link_integrity" in fired:
        add(
            "command_injection", 0.9,
            ["Uplink messages failed MAC authentication and were rejected by the vehicle",
             "Vehicle's target estimate still agrees with the ground track — no effect on flight"],
            ["Log and geolocate the injecting emitter", "Keep link authentication enforced",
             "Rotate uplink keys after the engagement"],
            ("link_integrity",),
        )
    elif "uplink_echo" in fired and not loss and not ({"track_quality", "telemetry_freshness"} & fired):
        add(
            "command_injection", 0.8,
            ["Vehicle reports a target position the ground never sent",
             "No packet loss and a clean ground track — the estimate was overwritten, not starved"],
            ["Enable uplink authentication; reject unauthenticated target updates",
             "Re-send authoritative target state and command re-acquisition",
             "If the vehicle cannot be recovered in time, abort the engagement over the safe area"],
            ("uplink_echo",),
        )
    if loss and "link_integrity" in fired:
        add(
            "uplink_jamming", 0.85,
            ["Uplink packet loss above threshold", "Target estimate drifting as the vehicle extrapolates stale updates"
             if "uplink_echo" in fired else "Target estimate still close — extrapolation holding for now"],
            ["Switch to the alternate uplink frequency / higher-power mode",
             "Hand over target updates to a second ground site", "Prepare a follow-up interceptor"],
            ("link_integrity",),
        )
    if "control_tracking" in fired:
        add(
            "actuator_stuck", 0.88,
            ["Measured lateral acceleration no longer follows the guidance command"]
            + (["Radar track has left the predicted corridor"] if "corridor" in fired else []),
            ["Command actuator reset / switch to the redundant actuator channel",
             "If the corridor breach continues, terminate the flight inside the safe area"],
            ("control_tracking",),
        )
    if "speed" in fired:
        add(
            "propulsion_degradation", 0.85,
            ["Radar speed below the pre-flight predicted profile"]
            + (["Zero-effort miss growing — kinematic reach is being lost"] if "predicted_miss" in fired else []),
            ["Re-evaluate reach against the target: if the intercept is unreachable, launch a follow-up",
             "Shorten time-to-go by re-planning the aim point"],
            ("speed",),
        )
    spoofed = "gnss_radar" in fired and "telemetry_freshness" not in fired
    if spoofed:
        quiet = "gnss_innovation" not in fired or first["gnss_innovation"]["t"] > first["gnss_radar"]["t"]
        add(
            "gnss_spoofing", 0.9 if quiet else 0.8,
            ["Raw GNSS fix disagrees with the independent radar track — the satellite signal is lying",
             "INS/GNSS innovation stayed quiet — the offset was introduced slowly, under the gate" if quiet
             else "INS/GNSS innovation also tripped — the drag-off was fast enough to exceed the gate"],
            ["Command the vehicle to reject GNSS and navigate on INS + radar uplink",
             "Flag the spoofed region to other assets", "Re-verify the corridor after nav reset"],
            ("gnss_radar",),
        )
    if "gnss_innovation" in fired and not spoofed and "telemetry_freshness" not in fired:
        add(
            "imu_gyro_drift", 0.78,
            ["GNSS corrections persistently large — INS dead-reckoning is drifting between fixes",
             "Raw GNSS fix still agrees with radar, so the inertial side is at fault"],
            ["Increase GNSS aiding gain / re-align the IMU in flight",
             "Watch the corridor: heading error grows with time since onset"],
            ("gnss_innovation",),
        )
    if "track_quality" in fired:
        add(
            "tracking_noise", 0.8,
            ["Ground target-track jitter above threshold", "Noisy target updates making guidance commands chatter"],
            ["Switch to the backup tracking radar", "Increase track filter smoothing before uplink"],
            ("track_quality",),
        )
    explained = {"corridor", "predicted_miss", "nav_integrity", "uplink_echo"}
    if not out and fired:
        trigger = tuple(sorted(fired, key=lambda k: first[k]["t"]))
        out.append(
            {
                "cause": None,
                "title": "Unattributed trajectory deviation",
                "category": "UNKNOWN",
                "confidence": 0.4,
                "t": when(*trigger),
                "evidence": [f"{k.replace('_', ' ')} tripped with no subsystem signature" for k in trigger]
                + ([] if fired - explained else ["Only outcome-level checks fired — the cause is not observable from the ground"]),
                "actions": ["Hold the engagement picture on radar", "Prepare a follow-up interceptor"],
            }
        )
    out.sort(key=lambda d: (d["t"] is None, d["t"] or 0.0))
    return out


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def _trim(sample: dict) -> dict:
    """Round floats so the JSON payload stays small."""
    def r(v):
        if isinstance(v, float):
            return round(v, 4)
        if isinstance(v, list):
            return [r(x) for x in v]
        return v
    return {k: r(v) for k, v in sample.items()}


def _outcome(flight: _Flight) -> dict:
    miss, t, x, y = flight.closest or (None, None, None, None)
    hit = miss is not None and miss <= LETHAL_RADIUS_KM
    return {
        "result": "INTERCEPT" if hit else "MISS",
        "miss_km": None if miss is None else round(miss, 4),
        "t": None if t is None else round(t, 3),
        "point": None if x is None else [round(x, 3), round(y, 3)],
        "end_reason": flight.end_reason,
        "lethal_radius_km": LETHAL_RADIUS_KM,
    }


def _preflight(cfg: EngagementConfig, nominal: _Flight) -> dict:
    outcome = _outcome(nominal)
    point = outcome["point"] or [0.0, 0.0]
    tof = (outcome["t"] or 0.0) - cfg.launch_delay_s
    margin = 1.0 - nominal.peak_lat / MAX_LAT_KMS2
    checks = [
        {
            "key": "predicted_intercept", "label": "Predicted intercept",
            "value": f"miss {outcome['miss_km'] * 1000:.0f} m" if outcome["miss_km"] is not None else "none",
            "limit": f"≤ {LETHAL_RADIUS_KM * 1000:.0f} m", "pass": outcome["result"] == "INTERCEPT",
        },
        {
            "key": "intercept_altitude", "label": "Intercept altitude",
            "value": f"{point[1]:.1f} km", "limit": f"≥ {MIN_INTERCEPT_ALT_KM:.0f} km (debris keep-out)",
            "pass": point[1] >= MIN_INTERCEPT_ALT_KM,
        },
        {
            "key": "intercept_range", "label": "Intercept ground range",
            "value": f"{point[0]:.1f} km", "limit": f"≤ {MAX_INTERCEPT_RANGE_KM:.0f} km",
            "pass": 0 <= point[0] <= MAX_INTERCEPT_RANGE_KM,
        },
        {
            "key": "time_of_flight", "label": "Time of flight",
            "value": f"{tof:.1f} s", "limit": f"≤ {ENDURANCE_S:.0f} s endurance", "pass": 0 < tof <= ENDURANCE_S,
        },
        {
            "key": "load_margin", "label": "Lateral load margin",
            "value": f"{margin * 100:.0f}% (peak {nominal.peak_lat / G_KMS2:.1f} g)",
            "limit": f"≥ {MIN_LOAD_MARGIN * 100:.0f}%", "pass": margin >= MIN_LOAD_MARGIN,
        },
    ]
    return {
        "go": all(c["pass"] for c in checks),
        "checks": checks,
        "predicted": {"t_intercept": outcome["t"], "point": outcome["point"], "miss_km": outcome["miss_km"],
                      "time_of_flight_s": round(tof, 2)},
    }


def simulate_engagement(cfg: EngagementConfig) -> dict:
    cfg.validate()
    nominal = _fly(cfg, faulty=False)
    flight = _fly(cfg, faulty=True) if cfg.fault else nominal
    events, first = _monitor(flight.samples, nominal.samples)
    diagnosis = _diagnose(first, flight.samples, cfg)
    outcome = _outcome(flight)

    fault = FAULTS.get(cfg.fault) if cfg.fault else None
    detection = None
    if fault is not None:
        first_t = min((e["t"] for e in events if e["t"] >= cfg.onset_s), default=None)
        top = diagnosis[0] if diagnosis else None
        detection = {
            "onset_s": cfg.onset_s,
            "first_alert_s": first_t,
            "latency_s": None if first_t is None else round(first_t - cfg.onset_s, 2),
            "diagnosed": top["cause"] if top else None,
            "correct": bool(top and top["cause"] == fault.key),
        }

    return {
        "config": cfg.__dict__,
        "fault": None if fault is None else fault.__dict__,
        "preflight": _preflight(cfg, nominal),
        "nominal": [{"t": s["t"], "x": round(s["radar"][0], 3), "y": round(s["radar"][1], 3), "launched": s["launched"]}
                    for s in nominal.samples],
        "samples": [_trim(s) for s in flight.samples],
        "events": events,
        "diagnosis": diagnosis,
        "detection": detection,
        "outcome": outcome,
        "checks": [{"key": k, "label": label, "description": d} for k, label, d in CHECKS],
        "limits": {
            "lethal_radius_km": LETHAL_RADIUS_KM, "min_intercept_alt_km": MIN_INTERCEPT_ALT_KM,
            "max_intercept_range_km": MAX_INTERCEPT_RANGE_KM,
        },
    }
