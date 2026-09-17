"""Custom rocket and satellite designs.

A `VehicleDesign` is a launcher (1–4 stages) plus the satellite it carries and a
target circular orbit. This module does three things with one:

* `analyse` — first-order performance: per-stage Tsiolkovsky Δv, thrust-to-weight,
  burn time, loss budget, orbit margin, and the satellite's energy balance.
* `build_plan` — turns the design into a `LaunchPlan`, so the 2D launch panel
  flies it with the same kinematic model as the reference vehicle. A design that
  cannot reach orbital speed is flown to burnout and aborts; it is never quietly
  given an orbit it did not earn.
* `generate` — sizes a plausible design from a plain-English request
  ("3-stage rocket for a 400 kg imaging satellite to 700 km") without an LLM.
  The API layer tries an LLM first and falls back to this.

Everything here is a first-order engineering estimate for concept testing. It is
not a substitute for trajectory optimisation or a qualified vehicle model.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, Field

from telemetry.launch import COUNTDOWN_S, THROTTLE_BUCKET, Burn, LaunchPlan  # noqa: F401

G0 = 9.80665
MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6371.0

# Reference bus (ASTRIX-01, 180 kg) per-kg sizing. The orbital simulator's
# telemetry is normalised to this bus, so a satellite sized exactly on these
# ratios produces reference telemetry and an undersized one shows its deficit.
REF_SOLAR_W_PER_KG = 236.0 / 180.0
REF_BATTERY_WH_PER_KG = 480.0 / 180.0
REF_BASE_LOAD_W_PER_KG = 118.0 / 180.0
REF_PAYLOAD_LOAD_W_PER_KG = 92.0 / 180.0

ORBIT_MARGIN_TARGET_MS = 250.0


class StageSpec(BaseModel):
    name: str = Field(default="Stage", max_length=40)
    thrust_kn: float = Field(gt=0.01, le=40_000.0, description="Vacuum-ish thrust of all engines, kN")
    isp_s: float = Field(ge=150.0, le=480.0, description="Specific impulse, seconds")
    propellant_t: float = Field(gt=0.001, le=4_000.0, description="Usable propellant, tonnes")
    dry_t: float = Field(gt=0.0005, le=400.0, description="Structure and engines, tonnes")
    engines: int = Field(default=1, ge=1, le=40)


class RocketSpec(BaseModel):
    name: str = Field(default="Custom LV", max_length=40)
    stages: list[StageSpec] = Field(min_length=1, max_length=4)
    fairing_t: float = Field(default=1.0, ge=0.0, le=20.0)
    color: str = Field(default="#e8e6dc", pattern=r"^#[0-9a-fA-F]{6}$")


class SatelliteSpec(BaseModel):
    name: str = Field(default="ASTRIX-01", max_length=40)
    bus: Literal["cubesat", "smallsat", "medium", "large"] = "smallsat"
    mass_kg: float = Field(default=180.0, ge=1.0, le=25_000.0)
    solar_w: float = Field(default=236.0, gt=0.0, le=40_000.0)
    battery_wh: float = Field(default=480.0, gt=0.0, le=100_000.0)
    base_load_w: float = Field(default=118.0, gt=0.0, le=30_000.0)
    payload_load_w: float = Field(default=92.0, ge=0.0, le=30_000.0)
    payload: str = Field(default="Earth-observation imager", max_length=60)
    reaction_wheels: int = Field(default=4, ge=3, le=6)
    color: str = Field(default="#3d6fb8", pattern=r"^#[0-9a-fA-F]{6}$")


class VehicleDesign(BaseModel):
    rocket: RocketSpec
    satellite: SatelliteSpec
    target_altitude_km: float = Field(default=500.0, ge=160.0, le=2_000.0)
    notes: str = Field(default="", max_length=400)


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #


def circular_speed_ms(altitude_km: float) -> float:
    return math.sqrt(MU_KM3_S2 / (EARTH_RADIUS_KM + altitude_km)) * 1000.0


def _stage_masses(design: VehicleDesign) -> list[tuple[float, float]]:
    """(ignition mass, burnout mass) per stage, tonnes."""
    payload_t = design.satellite.mass_kg / 1000.0
    stages = design.rocket.stages
    out = []
    for i, stage in enumerate(stages):
        above = sum(s.propellant_t + s.dry_t for s in stages[i + 1 :])
        # The fairing rides on stage 1 and is dropped early in stage 2.
        fairing = design.rocket.fairing_t if i == 0 else 0.0
        m0 = payload_t + above + fairing + stage.propellant_t + stage.dry_t
        out.append((m0, m0 - stage.propellant_t))
    return out


def loss_budget_ms(design: VehicleDesign) -> dict[str, float]:
    m0, _ = _stage_masses(design)[0]
    twr = design.rocket.stages[0].thrust_kn / (m0 * G0)
    gravity = 1250.0 * min(2.0, 1.35 / max(twr, 0.2))
    drag = 130.0
    climb = 0.45 * design.target_altitude_km  # potential energy of the altitude gained
    rotation = -350.0  # due-east launch from a mid-latitude site
    return {
        "gravity": round(gravity, 1),
        "drag": drag,
        "climb": round(climb, 1),
        "earth_rotation": rotation,
        "total": round(gravity + drag + climb + rotation, 1),
    }


def analyse(design: VehicleDesign) -> dict:
    masses = _stage_masses(design)
    stages = []
    total_dv = 0.0
    warnings: list[str] = []
    for i, (stage, (m0, mf)) in enumerate(zip(design.rocket.stages, masses)):
        dv = stage.isp_s * G0 * math.log(m0 / mf)
        mdot = stage.thrust_kn * 1000.0 / (stage.isp_s * G0)  # kg/s
        burn = stage.propellant_t * 1000.0 / mdot
        twr = stage.thrust_kn / (m0 * G0)
        a1_g = stage.thrust_kn / mf / G0
        total_dv += dv
        stages.append(
            {
                "index": i + 1,
                "name": stage.name,
                "ignition_mass_t": round(m0, 3),
                "burnout_mass_t": round(mf, 3),
                "delta_v_ms": round(dv, 1),
                "burn_time_s": round(burn, 1),
                "twr": round(twr, 3),
                "burnout_accel_g": round(a1_g, 2),
                "mass_ratio": round(m0 / mf, 3),
            }
        )
        if i == 0 and twr < 1.0:
            warnings.append(f"Stage 1 thrust-to-weight {twr:.2f} < 1: the vehicle cannot lift off.")
        elif i == 0 and twr < 1.15:
            warnings.append(f"Stage 1 thrust-to-weight {twr:.2f} is marginal; gravity losses will be high.")
        if a1_g > 6.0:
            warnings.append(f"{stage.name} reaches {a1_g:.1f} g at burnout — above the 6 g axial limit.")
        if burn > 900:
            warnings.append(f"{stage.name} burns for {burn:.0f} s; long burns raise gravity losses.")

    losses = loss_budget_ms(design)
    v_circ = circular_speed_ms(design.target_altitude_km)
    required = v_circ + losses["total"]
    margin = total_dv - required
    liftoff = stages[0]["twr"] >= 1.0
    feasible = liftoff and margin >= 0.0
    if liftoff and margin < 0:
        warnings.append(f"Δv shortfall of {-margin:.0f} m/s: the vehicle will not reach orbit.")
    elif feasible and margin < 0.6 * ORBIT_MARGIN_TARGET_MS:
        warnings.append(f"Orbit margin is only {margin:.0f} m/s; dispersions could leave it short.")

    sat = satellite_budget(design.satellite, design.target_altitude_km)
    warnings += sat["warnings"]
    gross = masses[0][0]
    return {
        "feasible": feasible,
        "gross_mass_t": round(gross, 3),
        "payload_fraction_pct": round(100.0 * design.satellite.mass_kg / 1000.0 / gross, 3),
        "total_delta_v_ms": round(total_dv, 1),
        "required_delta_v_ms": round(required, 1),
        "orbital_speed_ms": round(v_circ, 1),
        "margin_ms": round(margin, 1),
        "losses_ms": losses,
        "stages": stages,
        "satellite": sat,
        "warnings": warnings,
        "verdict": (
            "Reaches orbit" if feasible else ("Cannot lift off" if not liftoff else "Falls short of orbit")
        ),
        "disclaimer": "First-order estimate for concept testing only — verify with a qualified trajectory model.",
    }


def satellite_budget(sat: SatelliteSpec, altitude_km: float) -> dict:
    r = EARTH_RADIUS_KM + altitude_km
    period_s = 2 * math.pi * math.sqrt(r**3 / MU_KM3_S2)
    eclipse_fraction = math.asin(EARTH_RADIUS_KM / r) / math.pi
    sunlit_h = period_s * (1 - eclipse_fraction) / 3600.0
    eclipse_h = period_s * eclipse_fraction / 3600.0
    payload_duty = 0.21  # imaging window of the reference orbit schedule
    avg_load = sat.base_load_w + sat.payload_load_w * payload_duty
    generated_wh = sat.solar_w * 0.92 * sunlit_h
    consumed_wh = avg_load * (sunlit_h + eclipse_h)
    eclipse_dod = 100.0 * avg_load * eclipse_h / sat.battery_wh
    warnings = []
    if generated_wh < 0.97 * consumed_wh:
        warnings.append(
            f"{sat.name} is energy-negative ({generated_wh:.0f} Wh generated vs {consumed_wh:.0f} Wh used per orbit)."
        )
    if eclipse_dod > 40.0:
        warnings.append(f"Eclipse depth of discharge {eclipse_dod:.0f}% exceeds the 40% battery-life guideline.")
    return {
        "orbit_period_min": round(period_s / 60.0, 2),
        "eclipse_min": round(eclipse_h * 60.0, 2),
        "energy_generated_wh": round(generated_wh, 1),
        "energy_consumed_wh": round(consumed_wh, 1),
        "energy_margin_pct": round(100.0 * (generated_wh - consumed_wh) / consumed_wh, 1),
        "eclipse_dod_pct": round(eclipse_dod, 1),
        "warnings": warnings,
    }


def telemetry_factors(sat: SatelliteSpec | dict | None) -> dict[str, float]:
    """Per-unit quality of a satellite relative to the mass-scaled reference bus."""
    if sat is None:
        return {"solar": 1.0, "battery": 1.0, "base_load": 1.0, "payload_load": 1.0}
    if isinstance(sat, dict):
        sat = SatelliteSpec(**sat)
    m = sat.mass_kg
    return {
        "solar": sat.solar_w / (REF_SOLAR_W_PER_KG * m),
        "battery": sat.battery_wh / (REF_BATTERY_WH_PER_KG * m),
        "base_load": sat.base_load_w / (REF_BASE_LOAD_W_PER_KG * m),
        "payload_load": (sat.payload_load_w / (REF_PAYLOAD_LOAD_W_PER_KG * m)) if sat.payload_load_w else 0.0,
    }


# --------------------------------------------------------------------------- #
# Launch plan
# --------------------------------------------------------------------------- #


def build_plan(design: VehicleDesign) -> LaunchPlan:
    report = analyse(design)
    masses = _stage_masses(design)
    losses = report["losses_ms"]["total"]
    v_circ = report["orbital_speed_ms"]

    raw: list[dict] = []
    t = 0.0
    for i, (stage, (m0, mf), info) in enumerate(zip(design.rocket.stages, masses, report["stages"])):
        burn_s = info["burn_time_s"]
        raw.append({"stage": i + 1, "start": t, "full_end": t + burn_s, "a0": stage.thrust_kn / m0, "a1": stage.thrust_kn / mf})
        t = t + burn_s + 10.0  # 3 s to separation, 7 s to next ignition

    # Net speed gained per stage: scale the linear schedule so the whole flight
    # delivers exactly the Δv left after losses.
    linear_total = sum((b["a0"] + b["a1"]) / 2 * (b["full_end"] - b["start"]) for b in raw)
    efficiency = max(0.0, report["total_delta_v_ms"] - losses) / linear_total if linear_total else 0.0

    burns: list[Burn] = []
    speed = 0.0
    seco = raw[-1]["full_end"]
    reached = False
    for b in raw:
        duration = b["full_end"] - b["start"]
        stage_gain = (b["a0"] + b["a1"]) / 2 * duration * efficiency
        if speed + stage_gain >= v_circ:
            # Orbit is reached during this burn: solve the linear ramp for cut-off.
            need = (v_circ - speed) / efficiency
            slope = (b["a1"] - b["a0"]) / duration
            if abs(slope) < 1e-9:
                dt = need / b["a0"]
            else:
                dt = (-b["a0"] + math.sqrt(b["a0"] ** 2 + 2 * slope * need)) / slope
            dt = max(1.0, min(duration, dt))
            end = b["start"] + dt
            burns.append(Burn(b["stage"], b["start"], end, b["full_end"], b["a0"], b["a0"] + slope * dt, efficiency))
            seco = end
            reached = True
            break
        speed += stage_gain
        burns.append(Burn(b["stage"], b["start"], b["full_end"], b["full_end"], b["a0"], b["a1"], efficiency))

    separations = tuple(burn.end + 3.0 for burn in burns[:-1])
    if len(burns) >= 2:
        fairing = min(burns[1].start + 45.0, seco - 5.0)
    else:
        fairing = 0.45 * seco
    fairing = max(fairing, min(seco * 0.5, 60.0))
    liftoff_failure = report["stages"][0]["twr"] < 1.0
    return LaunchPlan(
        burns=tuple(burns),
        separations=separations,
        fairing_sep_s=round(fairing, 1),
        seco_s=seco,
        payload_sep_s=seco + 40.0,
        arrays_s=seco + 70.0,
        detumble_s=seco + 100.0,
        target_altitude_km=design.target_altitude_km,
        orbital_speed_kms=round(v_circ / 1000.0, 4),
        throttle_bucket=THROTTLE_BUCKET,
        name=design.rocket.name,
        payload_name=design.satellite.name,
        shortfall=not reached and not liftoff_failure,
        liftoff_failure=liftoff_failure,
    )


# --------------------------------------------------------------------------- #
# Presets and generation
# --------------------------------------------------------------------------- #


def _satellite_for_mass(mass_kg: float, name: str = "ASTRIX-01", payload: str = "Earth-observation imager") -> SatelliteSpec:
    bus = "cubesat" if mass_kg <= 30 else "smallsat" if mass_kg <= 500 else "medium" if mass_kg <= 2500 else "large"
    return SatelliteSpec(
        name=name,
        bus=bus,
        mass_kg=round(mass_kg, 1),
        solar_w=round(REF_SOLAR_W_PER_KG * mass_kg, 1),
        battery_wh=round(REF_BATTERY_WH_PER_KG * mass_kg, 1),
        base_load_w=round(REF_BASE_LOAD_W_PER_KG * mass_kg, 1),
        payload_load_w=round(REF_PAYLOAD_LOAD_W_PER_KG * mass_kg, 1),
        payload=payload,
    )


# Stage templates per stage count, sized for roughly 1 t to LEO at scale 1.0.
_TEMPLATES: dict[int, list[dict]] = {
    1: [dict(name="Core", thrust_kn=1400.0, isp_s=330.0, propellant_t=110.0, dry_t=5.5)],
    2: [
        dict(name="Booster", thrust_kn=1450.0, isp_s=290.0, propellant_t=86.0, dry_t=6.2),
        dict(name="Upper stage", thrust_kn=110.0, isp_s=345.0, propellant_t=18.0, dry_t=1.6),
    ],
    3: [
        dict(name="Booster", thrust_kn=1500.0, isp_s=280.0, propellant_t=72.0, dry_t=5.8),
        dict(name="Second stage", thrust_kn=330.0, isp_s=315.0, propellant_t=20.0, dry_t=1.9),
        dict(name="Kick stage", thrust_kn=40.0, isp_s=335.0, propellant_t=3.2, dry_t=0.45),
    ],
    4: [
        dict(name="Booster", thrust_kn=1500.0, isp_s=275.0, propellant_t=70.0, dry_t=5.6),
        dict(name="Second stage", thrust_kn=340.0, isp_s=300.0, propellant_t=19.0, dry_t=1.8),
        dict(name="Third stage", thrust_kn=60.0, isp_s=320.0, propellant_t=4.2, dry_t=0.55),
        dict(name="Kick stage", thrust_kn=9.0, isp_s=330.0, propellant_t=0.6, dry_t=0.1),
    ],
}


def _scaled(template: list[dict], scale: float, name: str, fairing_t: float) -> RocketSpec:
    stages = [
        StageSpec(
            name=s["name"],
            thrust_kn=round(s["thrust_kn"] * scale, 2),
            isp_s=s["isp_s"],
            propellant_t=round(s["propellant_t"] * scale, 3),
            dry_t=round(s["dry_t"] * scale, 3),
        )
        for s in template
    ]
    return RocketSpec(name=name, stages=stages, fairing_t=round(fairing_t, 3))


def size_rocket(satellite: SatelliteSpec, altitude_km: float, stages: int = 2, name: str = "Custom LV") -> VehicleDesign:
    """Smallest scaling of the stage template that reaches orbit with margin."""
    template = _TEMPLATES[max(1, min(4, stages))]
    fairing = max(0.03, 0.12 * (satellite.mass_kg / 1000.0) ** 0.8 + 0.05)

    def design(scale: float) -> VehicleDesign:
        return VehicleDesign(
            rocket=_scaled(template, scale, name, fairing), satellite=satellite, target_altitude_km=altitude_km
        )

    def margin(scale: float) -> float:
        return analyse(design(scale))["margin_ms"]

    lo, hi = 0.004, 24.0  # keeps every scaled stage inside StageSpec bounds
    if margin(hi) < ORBIT_MARGIN_TARGET_MS:
        return design(hi)  # infeasible at any size; analysis explains why
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        if margin(mid) >= ORBIT_MARGIN_TARGET_MS:
            hi = mid
        else:
            lo = mid
    return design(hi)


_WORD_NUMBERS = {"single": 1, "one": 1, "two": 2, "three": 3, "four": 4}


def generate(prompt: str) -> VehicleDesign:
    """Deterministic design generator for plain-English requests."""
    text = prompt.lower()

    altitude = 500.0
    if m := re.search(r"(\d{3,4}(?:\.\d+)?)\s*-?\s*km", text):
        altitude = float(m.group(1))
    elif "iss" in text:
        altitude = 420.0
    elif "sun-synchronous" in text or re.search(r"\bsso\b", text):
        altitude = 600.0
    elif "vleo" in text or "very low" in text:
        altitude = 250.0
    altitude = min(2000.0, max(160.0, altitude))

    mass = None
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilograms?|t\b|tons?|tonnes?)", text):
        value = float(m.group(1))
        mass = value if m.group(2).startswith("k") else value * 1000.0
    if mass is None:
        if "cubesat" in text:
            mass = 12.0
        elif "heavy" in text or "large" in text:
            mass = 6000.0
        elif "medium" in text:
            mass = 1200.0
        elif "small" in text or "micro" in text:
            mass = 150.0
        else:
            mass = 180.0
    mass = min(25_000.0, max(1.0, mass))

    stages = 2
    if m := re.search(r"(\d)\s*-?\s*stages?", text):
        stages = int(m.group(1))
    else:
        for word, n in _WORD_NUMBERS.items():
            if re.search(rf"\b{word}[\s-]*stages?", text):
                stages = n
                break
    stages = max(1, min(4, stages))

    payload = "Earth-observation imager"
    for key, label in (
        ("comm", "Communications transponder"),
        ("radar", "Synthetic-aperture radar"),
        ("sar", "Synthetic-aperture radar"),
        ("weather", "Weather sounder"),
        ("science", "Science instrument"),
        ("telescope", "Space telescope"),
        ("iot", "IoT relay"),
        ("navigation", "Navigation payload"),
    ):
        if key in text:
            payload = label
            break

    sat_name = "ASTRIX-01"
    rocket_name = "Custom LV"
    if m := re.search(r"(?:called|named)\s+([a-z0-9][a-z0-9\-]{1,30})", text):
        sat_name = m.group(1).upper()
    if m := re.search(r"rocket\s+(?:called|named)\s+([a-z0-9][a-z0-9\-]{1,30})", text):
        rocket_name = m.group(1).upper()
        if sat_name == rocket_name:
            sat_name = "ASTRIX-01"
    if rocket_name == "Custom LV":
        cls = "Micro" if mass <= 30 else "Light" if mass <= 500 else "Medium" if mass <= 3000 else "Heavy"
        rocket_name = f"Astrix {cls}-{stages}"

    satellite = _satellite_for_mass(mass, sat_name, payload)
    design = size_rocket(satellite, altitude, stages, rocket_name)
    design.notes = f"Generated from: {prompt.strip()[:300]}"
    return design


PRESETS: dict[str, VehicleDesign] = {}


def _register_presets() -> None:
    PRESETS["astrix-reference"] = VehicleDesign(
        rocket=RocketSpec(
            name="ASTRIX-LV",
            stages=[
                StageSpec(name="Booster", thrust_kn=1000.0, isp_s=290.0, propellant_t=58.0, dry_t=4.6),
                StageSpec(name="Upper stage", thrust_kn=70.0, isp_s=345.0, propellant_t=11.0, dry_t=1.1),
            ],
            fairing_t=0.9,
        ),
        satellite=_satellite_for_mass(180.0),
        target_altitude_km=500.0,
        notes="Two-stage reference launcher with the ASTRIX-01 smallsat.",
    )
    PRESETS["cubesat-rideshare"] = generate("2-stage rocket for a 12 kg cubesat called ORBIT-CUBE to 550 km")
    PRESETS["heavy-comsat"] = generate("3-stage heavy rocket for a 4500 kg communications satellite called COMSTAR to 800 km")
    PRESETS["underpowered-demo"] = VehicleDesign(
        rocket=RocketSpec(
            name="Stubby-1",
            stages=[
                StageSpec(name="Booster", thrust_kn=900.0, isp_s=270.0, propellant_t=60.0, dry_t=8.0),
                StageSpec(name="Upper stage", thrust_kn=60.0, isp_s=320.0, propellant_t=6.0, dry_t=1.2),
            ],
            fairing_t=0.8,
        ),
        satellite=_satellite_for_mass(400.0, "LEAD-SAT"),
        target_altitude_km=600.0,
        notes="Deliberately undersized: demonstrates a Δv shortfall and ascent abort.",
    )


_register_presets()
