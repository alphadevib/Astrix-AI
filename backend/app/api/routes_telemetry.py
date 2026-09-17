"""Telemetry ingest and the full-loop endpoint."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, status

from ..core.schemas import LoopResult, TelemetryFrame
from .deps import AstrixDep, PipelineDep

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", summary="Ingest one telemetry frame and run the ASTRIX loop")
async def ingest(frame: TelemetryFrame, pipeline: PipelineDep, astrix: AstrixDep) -> dict:
    """The spacecraft's entry point.

    The response carries any approved commands for the spacecraft to apply, which
    is how an approved recovery actually reaches the vehicle: ASTRIX never reaches
    into the spacecraft, the spacecraft collects its instructions.
    """
    _refuse_if_simulated(astrix, frame)
    errors = pipeline.buffer.validate(frame)
    # `ingest` is CPU-bound and may make a blocking LLM call, so keep it off the loop.
    result = await asyncio.to_thread(pipeline.ingest, frame)
    return {
        "accepted": True,
        "seq": frame.seq,
        "validation_errors": errors,
        "window_size": pipeline.buffer.size(frame.spacecraft_id),
        "loop": result.model_dump(mode="json"),
        "commands": pipeline.pop_commands(frame.spacecraft_id),
    }


@router.post(
    "/pipeline/run",
    response_model=LoopResult,
    summary="Run the full loop on a frame and return only the loop result",
)
async def run_pipeline(frame: TelemetryFrame, pipeline: PipelineDep, astrix: AstrixDep) -> LoopResult:
    _refuse_if_simulated(astrix, frame)
    return await asyncio.to_thread(pipeline.ingest, frame)


def _refuse_if_simulated(astrix, frame: TelemetryFrame) -> None:
    """Two telemetry sources for one spacecraft would interleave sequence numbers,
    reset each other's session and steal each other's commands."""
    runner = astrix.runner
    if runner is not None and runner.running and runner.spacecraft_id == frame.spacecraft_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"the in-process mission is already flying {frame.spacecraft_id}; stop it "
                "(POST /mission/stop) before streaming external telemetry for that spacecraft"
            ),
        )


@router.get("/telemetry/latest", summary="Most recent frame for a spacecraft")
async def latest(astrix: AstrixDep, spacecraft_id: str = "ASTRIX-01") -> dict:
    frame = astrix.buffer.latest(spacecraft_id)
    return {
        "spacecraft_id": spacecraft_id,
        "frame": frame.model_dump(mode="json") if frame else None,
        "window_size": astrix.buffer.size(spacecraft_id),
    }


@router.get("/telemetry/history", summary="Rolling telemetry window for charting")
async def history(
    astrix: AstrixDep,
    spacecraft_id: str = "ASTRIX-01",
    limit: int = Query(default=120, ge=1, le=1000),
) -> dict:
    frames = astrix.buffer.history(spacecraft_id, limit=limit)
    return {
        "spacecraft_id": spacecraft_id,
        "count": len(frames),
        "frames": [f.model_dump(mode="json") for f in frames],
    }
