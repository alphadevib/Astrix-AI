"""ASTRIX — Autonomous Spacecraft Intelligence & eXecution.

    Detect. Reason. Recover. Learn.

FastAPI application entry point.

Run from the repository root so the `telemetry` package is importable:

    uvicorn backend.app.main:app --reload --port 8000

First start trains the anomaly detector on simulated fault-free telemetry and
seeds mission memory; both are cached, so subsequent starts are immediate.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import routes_control, routes_stages, routes_telemetry, ws
from .bootstrap import Astrix
from .config import get_settings
from .services.mission_runner import MissionRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-34s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("astrix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("=" * 78)
    log.info("  %s — %s", settings.app_name, settings.tagline)
    log.info("  Autonomous Spacecraft Intelligence & eXecution")
    log.info("=" * 78)

    # Construction does real work (detector training on a cold start), so keep it
    # off the event loop.
    astrix = await asyncio.to_thread(Astrix, settings)
    astrix.bus.bind_loop(asyncio.get_running_loop())
    astrix.runner = MissionRunner(astrix.pipeline)
    app.state.astrix = astrix

    log.info("ASTRIX ready — POST /mission/start to begin a simulated mission")
    try:
        yield
    finally:
        if astrix.runner is not None:
            await astrix.runner.stop()
        astrix.vectors.persist()
        astrix.engine.dispose()
        log.info("ASTRIX shut down")


settings = get_settings()

app = FastAPI(
    title="ASTRIX",
    version="0.1.0",
    summary="Autonomous Spacecraft Intelligence & eXecution",
    description=(
        "An agentic AI system for spacecraft anomaly detection, recovery and mission learning.\n\n"
        "**Loop:** Detect → Diagnose → Remember → Plan → Simulate → Verify → Recover → Learn\n\n"
        "`POST /telemetry` runs the whole loop for one frame. The `/anomaly/detect`, "
        "`/diagnose`, `/risk-assessment`, `/recovery/*` and `/mission-memory/learn` endpoints "
        "expose the same stages individually, which is how the n8n workflows drive them.\n\n"
        "AI agents propose; a deterministic safety engine and a digital twin verify; "
        "high-risk actions require human approval."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_telemetry.router)
app.include_router(routes_stages.router)
app.include_router(routes_control.router)
app.include_router(ws.router)


@app.get("/", tags=["control"], summary="Service banner")
async def root() -> dict:
    return {
        "app": "ASTRIX",
        "expansion": "Autonomous Spacecraft Intelligence & eXecution",
        "tagline": settings.tagline,
        "loop": "Detect → Diagnose → Remember → Plan → Simulate → Verify → Recover → Learn",
        "docs": "/docs",
        "websocket": "/ws/telemetry",
    }
