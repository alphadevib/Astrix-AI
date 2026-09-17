"""Interceptor trajectory check and engagement anomaly analysis.

Stateless: every call flies the pre-flight reference and the (optionally faulted)
engagement from scratch, so the dashboard can re-run it on every drag of the
target without any server-side session.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from telemetry.intercept import CHECKS, FAULTS, EngagementConfig, simulate_engagement

router = APIRouter(prefix="/intercept", tags=["intercept"])


class EngagementRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_x_km: float = Field(default=150.0, gt=10.0, le=300.0)
    target_y_km: float = Field(default=70.0, gt=1.0, le=150.0)
    target_vx_kms: float = Field(default=-2.0, ge=-5.0, le=0.0)
    target_vy_kms: float = Field(default=-0.5, ge=-3.0, le=3.0)
    target_weave_kms2: float = Field(default=0.01, ge=0.0, le=0.08)
    launch_delay_s: float = Field(default=4.0, ge=0.0, le=40.0)
    fault: str | None = None
    onset_s: float = Field(default=12.0, ge=0.0, le=90.0)
    severity: float = Field(default=0.6, gt=0.0, le=1.0)
    link_authentication: bool = True
    seed: int = Field(default=7, ge=0, le=1_000_000)


@router.get("/faults", summary="Injectable interceptor faults and cyber attacks")
async def faults() -> dict:
    return {
        "faults": [f.__dict__ for f in FAULTS.values()],
        "checks": [{"key": k, "label": label, "description": d} for k, label, d in CHECKS],
    }


@router.post("/simulate", summary="Pre-flight trajectory check plus a monitored engagement")
async def simulate(body: EngagementRequest) -> dict:
    cfg = EngagementConfig(**body.model_dump())
    try:
        return await asyncio.to_thread(simulate_engagement, cfg)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc).strip("'")) from None
