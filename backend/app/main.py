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
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .api import (
    routes_assistant,
    routes_auth,
    routes_control,
    routes_hardware,
    routes_intercept,
    routes_model,
    routes_stages,
    routes_telemetry,
    routes_vehicles,
    ws,
)
from .core import disclaimer
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
    astrix.runner = MissionRunner(
        astrix.pipeline, hardware=astrix.hardware, model_lab=astrix.model_lab
    )
    app.state.astrix = astrix

    log.info("ASTRIX ready — POST /mission/start to begin a simulated mission")
    try:
        yield
    finally:
        if astrix.runner is not None:
            await astrix.runner.stop()
        astrix.vectors.persist()
        astrix.engine.dispose()
        if astrix.model_lab is not None:
            astrix.model_lab.corpus.close()
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

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Reachable without an account: the sign-in surface itself, liveness probes and
# the API's own documentation. Everything else needs a session.
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/meta",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/auth/login",
        "/auth/register",
    }
)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-astrix-token", "").strip()


@app.middleware("http")
async def guard_and_notice(request: Request, call_next):
    """Account gate for every non-public route, plus the standing results notice.

    The console is account-gated end to end: a reader without a session sees no
    telemetry, no mission memory and no audit trail, not just no write access.
    Two credentials are accepted — an operator's session token from `/auth/login`,
    and the optional `ASTRIX_API_TOKEN` machine credential for automation that
    cannot hold an interactive session.
    """
    path = request.url.path
    public = path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/static")

    if not public and request.method != "OPTIONS":
        supplied = _bearer_token(request)
        machine = settings.api_token
        authorised = bool(machine) and hmac.compare_digest(supplied.encode(), machine.encode())
        if not authorised and supplied:
            astrix = getattr(app.state, "astrix", None)
            authorised = astrix is not None and astrix.auth.resolve(supplied) is not None
        if not authorised and settings.require_auth:
            return JSONResponse(
                {"detail": "Sign in to continue."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not authorised and machine and request.method in MUTATING:
            # Auth is off, but a machine credential is configured: writes still need it.
            return JSONResponse({"detail": "missing or invalid API token"}, status_code=401)

    response = await call_next(request)
    response.headers["X-Astrix-Notice"] = disclaimer.SHORT
    return response


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Astrix-Notice"],
)

app.include_router(routes_auth.router)
app.include_router(routes_telemetry.router)
app.include_router(routes_stages.router)
app.include_router(routes_control.router)
app.include_router(routes_intercept.router)
app.include_router(routes_vehicles.router)
app.include_router(routes_hardware.router)
app.include_router(routes_model.router)
app.include_router(routes_assistant.router)
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
        "notice": disclaimer.FULL,
    }


@app.get("/meta", tags=["control"], summary="Deployment metadata and the results disclaimer")
async def meta() -> dict:
    return {
        "app": settings.app_name,
        "version": app.version,
        "auth_required": settings.require_auth,
        "registration_open": settings.allow_registration,
        "notice": disclaimer.SHORT,
        "disclaimer": disclaimer.FULL,
    }
