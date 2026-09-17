"""Hardware-in-the-loop endpoints for Arduino-class prototypes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..hardware import HardwareReading
from .deps import AstrixDep

router = APIRouter(prefix="/hardware", tags=["hardware"])


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    readings: list[HardwareReading] = Field(default_factory=list, max_length=200)
    acks: list[str] = Field(default_factory=list, max_length=100)
    transport: str = Field(default="web-serial", max_length=32)


class CommandRequest(BaseModel):
    command: str = Field(min_length=2, max_length=64)
    # "queue": deliver with the next telemetry response (bridge / remote board).
    # "local": the browser already wrote it to the port; just record it.
    delivery: str = Field(default="queue", pattern="^(queue|local)$")


class OverlayRequest(BaseModel):
    enabled: bool


@router.get("/status", summary="Device, calibration and command log")
async def hardware_status(astrix: AstrixDep) -> dict:
    return astrix.hardware.status()


@router.get("/history", summary="Recent raw readings from the device")
async def hardware_history(astrix: AstrixDep, limit: int = Query(default=120, ge=1, le=300)) -> dict:
    return {"readings": astrix.hardware.history(limit)}


@router.post("/telemetry", summary="Ingest device readings; returns commands to write to the device")
async def hardware_telemetry(body: TelemetryBatch, astrix: AstrixDep) -> dict:
    return astrix.hardware.ingest(body.readings, transport=body.transport, acks=body.acks)


@router.post("/command", summary="Send a command to the device")
async def hardware_command(body: CommandRequest, astrix: AstrixDep) -> dict:
    try:
        if body.delivery == "local":
            return astrix.hardware.record_local(body.command)
        return astrix.hardware.queue(body.command)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None


@router.post("/calibrate", summary="Re-learn the nominal baseline from the next readings")
async def hardware_calibrate(astrix: AstrixDep) -> dict:
    astrix.hardware.recalibrate()
    return astrix.hardware.status()


@router.post("/overlay", summary="Enable or disable blending readings into spacecraft telemetry")
async def hardware_overlay(body: OverlayRequest, astrix: AstrixDep) -> dict:
    astrix.hardware.overlay_enabled = body.enabled
    return astrix.hardware.status()


@router.post("/disconnect", summary="Forget the device and its baseline")
async def hardware_disconnect(astrix: AstrixDep) -> dict:
    astrix.hardware.reset()
    return astrix.hardware.status()
