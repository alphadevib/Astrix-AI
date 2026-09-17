"""Mission control, memory inspection and system status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..core.enums import Subsystem
from ..core.schemas import SpacecraftProfile
from .deps import AstrixDep, PipelineDep, RunnerDep
from .requests import MissionStartRequest

router = APIRouter(tags=["control"])


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


@router.get("/health", summary="Liveness and component status")
async def health(astrix: AstrixDep) -> dict:
    return astrix.health()


@router.get("/status", summary="Loop, autonomy and reasoner status")
async def status_(astrix: AstrixDep, pipeline: PipelineDep) -> dict:
    return {
        "pipeline": pipeline.status(),
        "mission": astrix.runner.status() if astrix.runner else {"running": False},
        "memory": astrix.memory.stats(),
        "subscribers": astrix.bus.subscriber_count,
    }


@router.get("/events/recent", summary="Recent agent-activity events (WebSocket replay)")
async def recent_events(astrix: AstrixDep, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"events": astrix.bus.recent(limit)}


# --------------------------------------------------------------------------- #
# Mission simulator control
# --------------------------------------------------------------------------- #


@router.post("/mission/start", summary="Start the in-process spacecraft simulator")
async def mission_start(body: MissionStartRequest, runner: RunnerDep) -> dict:
    try:
        return await runner.start(
            interval=body.interval,
            dt=body.dt,
            scenario=body.scenario,
            settle_frames=body.settle_frames,
            seed=body.seed,
            include_launch=body.include_launch,
            launch_time_scale=body.launch_time_scale,
        )
    except KeyError as exc:  # unknown scenario key
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None


@router.post("/mission/stop", summary="Stop the in-process spacecraft simulator")
async def mission_stop(runner: RunnerDep) -> dict:
    return await runner.stop()


@router.get("/mission/status", summary="Simulated spacecraft state")
async def mission_status(runner: RunnerDep) -> dict:
    return runner.status()


@router.get("/scenarios", summary="Available fault scenarios")
async def scenarios() -> dict:
    from telemetry.scenarios import BENIGN_SCENARIOS, SCENARIOS

    return {
        "scenarios": [
            {
                "key": s.key,
                "title": s.title,
                "subsystem": s.subsystem,
                "description": s.description,
                "ramp_seconds": s.ramp_seconds,
                "expected_signals": list(s.expected_signals),
                # The benign case is not a fault; the dashboard labels it differently.
                "is_fault": s.key not in BENIGN_SCENARIOS,
            }
            for s in sorted(SCENARIOS.values(), key=lambda s: s.key)
        ]
    }


@router.get("/launch/timeline", summary="Launch and deployment milestones")
async def launch_timeline() -> dict:
    from telemetry.launch import END_S, TARGET_ALTITUDE_KM, timeline

    return {"milestones": timeline(), "end_t": END_S, "target_altitude_km": TARGET_ALTITUDE_KM}


@router.post("/mission/inject/{scenario_key}", summary="Inject a fault into the running mission")
async def inject(scenario_key: str, runner: RunnerDep) -> dict:
    try:
        return runner.inject(scenario_key)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.post("/mission/clear-fault", summary="Clear the injected fault")
async def clear_fault(runner: RunnerDep) -> dict:
    try:
        return runner.clear_fault()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


# --------------------------------------------------------------------------- #
# Memory inspection
# --------------------------------------------------------------------------- #


@router.get("/anomalies", summary="Recent anomalies, including suppressed ones")
async def anomalies(
    astrix: AstrixDep,
    spacecraft_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
) -> dict:
    return {"anomalies": astrix.memory.open_anomalies(spacecraft_id, limit=limit)}


@router.get(
    "/spacecraft/{spacecraft_id}/profile",
    response_model=SpacecraftProfile,
    summary="Assembled spacecraft knowledge profile",
)
async def profile(spacecraft_id: str, astrix: AstrixDep) -> SpacecraftProfile:
    return astrix.memory.profile(spacecraft_id)


@router.get("/lessons", summary="Lessons learned on record")
async def lessons(astrix: AstrixDep, subsystem: str | None = None) -> dict:
    scope = None
    if subsystem:
        try:
            scope = Subsystem(subsystem.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown subsystem '{subsystem}'",
            ) from None
    return {"lessons": astrix.memory.relevant_lessons(scope, limit=20)}


@router.get("/audit", summary="Append-only verification audit trail")
async def audit(
    astrix: AstrixDep,
    cycle_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {"audit": astrix.memory.audit_trail(cycle_id, limit=limit)}


# --------------------------------------------------------------------------- #
# Safety configuration
# --------------------------------------------------------------------------- #


@router.get("/safety/limits", summary="Active safety limits and action tiers")
async def safety_limits(astrix: AstrixDep) -> dict:
    from ..agents.knowledge import ACTION_CATALOG
    from ..safety.engine import RULES

    return {
        "limits": astrix.safety.limits,
        "autonomy_limit": astrix.settings.auto_execute_max_risk.value,
        "rules": [{"rule_id": r.rule_id, "description": r.description} for r in RULES],
        "actions": [
            {
                "action_id": spec.action_id,
                "description": spec.description,
                "risk_level": spec.risk_level.value,
                "subsystem": spec.subsystem.value,
                "expected_effect": spec.expected_effect,
                "preconditions": list(spec.preconditions),
            }
            for spec in ACTION_CATALOG.values()
        ],
    }


@router.post("/safety/reload", summary="Re-read limits.yaml without restarting")
async def safety_reload(astrix: AstrixDep) -> dict:
    """Useful when a jury asks "what if the floor were 25%?" — edit and reload."""
    astrix.safety.reload()
    return {"reloaded": True, "limits": astrix.safety.limits}
