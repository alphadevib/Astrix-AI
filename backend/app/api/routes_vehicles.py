"""Vehicle Studio: design, analyse and generate custom rockets and satellites."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from telemetry.launch import LaunchProfile
from telemetry.vehicles import PRESETS, VehicleDesign, analyse, build_plan, generate

from ..core.disclaimer import FULL as DISCLAIMER
from .deps import AstrixDep

router = APIRouter(prefix="/vehicles", tags=["vehicles"])
log = logging.getLogger(__name__)

GENERATOR_SYSTEM = (
    "You are a launch-vehicle concept designer. Produce a physically plausible design for the "
    "request: 1-4 stages, realistic thrust (kN), Isp (s), propellant and dry mass (tonnes), and a "
    "satellite whose power budget suits its mass. Stage 1 thrust-to-weight should be 1.2-1.6 and "
    "the total ideal delta-v should exceed orbital speed plus ~1.5 km/s of losses. Use metric units."
)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt: str = Field(min_length=3, max_length=600)
    use_llm: bool = True


def preview(design: VehicleDesign, step: float = 4.0) -> dict:
    """Fly the ascent once, sampled for the Studio's trajectory preview."""
    plan = build_plan(design)
    profile = LaunchProfile(plan=plan)
    track = []
    last = None
    while not profile.finished:
        snap = profile.step(step)
        last = snap
        track.append(
            {
                "t": round(snap.t, 1),
                "alt": round(snap.altitude_km, 2),
                "down": round(snap.downrange_km, 2),
                "speed": round(snap.speed_kms, 3),
                "q": round(snap.dynamic_pressure_kpa, 2),
                "g": round(snap.acceleration_g, 2),
            }
        )
    return {
        "plan": plan.summary(),
        "track": track,
        "aborted": profile.aborted,
        "abort_reason": profile.fault if profile.aborted else None,
        "final": last.as_dict() if last else None,
    }


def _report(design: VehicleDesign, source: str) -> dict:
    return {
        "design": design.model_dump(),
        "analysis": analyse(design),
        "preview": preview(design),
        "source": source,
        "disclaimer": DISCLAIMER,
    }


@router.get("/presets", summary="Built-in vehicle designs")
async def presets() -> dict:
    return {
        "presets": [
            {"key": key, "name": f"{d.rocket.name} + {d.satellite.name}", "notes": d.notes, "design": d.model_dump()}
            for key, d in PRESETS.items()
        ]
    }


@router.post("/analyse", summary="Performance analysis and ascent preview for a design")
async def analyse_design(design: VehicleDesign) -> dict:
    return await asyncio.to_thread(_report, design, "manual")


@router.post("/generate", summary="Generate a design from a plain-English request")
async def generate_design(body: GenerateRequest, astrix: AstrixDep) -> dict:
    design = None
    source = "deterministic"
    llm = astrix.llm
    if body.use_llm and llm.available:
        draft = await asyncio.to_thread(
            llm.structured, GENERATOR_SYSTEM, body.prompt, VehicleDesign, "vehicle-designer", 2048, "deep"
        )
        if draft is not None:
            try:
                design = VehicleDesign(**draft.model_dump())
                source = f"llm:{llm.gateway.last_provider}"
            except ValidationError:
                design = None
    if design is None:
        design = await asyncio.to_thread(generate, body.prompt)
    design.notes = design.notes or f"Generated from: {body.prompt[:300]}"
    return await asyncio.to_thread(_report, design, source)
