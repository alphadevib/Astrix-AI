"""Per-stage endpoints.

These exist so n8n can orchestrate the loop node by node and the agent workflow is
visible during the demo (master context §14). Each endpoint is one stage of
Detect → Diagnose → Remember → Plan → Simulate → Verify → Recover → Learn, and
each is independently callable with an explicit input body.

`/recovery/verify` and `/recovery/simulate` are separate endpoints on purpose.
The safety engine and the digital twin are separate authorities, and an n8n
workflow that skipped one would be visibly missing a node.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status

from ..agents.knowledge import ACTION_CATALOG
from ..core.enums import Outcome
from ..core.schemas import (
    ApprovalRequest,
    ApprovalResult,
    DetectionResult,
    Diagnosis,
    MissionLearning,
    RecoveryOption,
    RecoveryPlan,
    ResourceState,
    RiskAssessment,
    SafetyVerdict,
    SimulationResult,
    TelemetryFrame,
)
from .deps import AstrixDep, PipelineDep
from .requests import (
    ActionRequest,
    DiagnoseRequest,
    LearnRequest,
    RecoveryGenerateRequest,
    RiskRequest,
)

router = APIRouter(tags=["stages"])


# --------------------------------------------------------------------------- #
# Detect
# --------------------------------------------------------------------------- #


@router.post("/anomaly/detect", response_model=DetectionResult, summary="Stage 1 — detection")
async def detect(frame: TelemetryFrame, pipeline: PipelineDep) -> DetectionResult:
    """Score a frame without opening an anomaly or touching mission memory.

    Note this does not add the frame to the rolling window, so the persistence
    term is whatever the window already holds. Use `POST /telemetry` for the
    stateful path.
    """
    output = pipeline.detector.score(frame)
    if not output.fitted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the anomaly detector is not trained yet",
        )
    return pipeline.scorer.score(
        frame=frame,
        ml_score=output.score,
        deviating_parameters=output.deviating_parameters,
        recall=None,
        persistence_seconds=0.0,
        window_size=pipeline.buffer.size(frame.spacecraft_id),
    )


# --------------------------------------------------------------------------- #
# Remember
# --------------------------------------------------------------------------- #


@router.get("/mission-memory", summary="Stage 2 — retrieve comparable mission history")
async def recall(
    astrix: AstrixDep,
    query: str,
    subsystem: str | None = None,
    k: int = 4,
) -> dict:
    from ..core.enums import Subsystem

    scope = None
    if subsystem:
        try:
            scope = Subsystem(subsystem.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown subsystem '{subsystem}'",
            ) from None

    result = astrix.memory.recall(
        query=query,
        subsystem=scope,
        k=k,
        min_similarity=astrix.settings.vector_min_similarity,
    )
    return result.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Diagnose
# --------------------------------------------------------------------------- #


@router.post("/diagnose", response_model=Diagnosis, summary="Stage 3 — diagnosis")
async def diagnose(body: DiagnoseRequest, pipeline: PipelineDep) -> Diagnosis:
    return await asyncio.to_thread(
        pipeline.diagnostic_agent.run, body.frame, body.detection, body.recall
    )


# --------------------------------------------------------------------------- #
# Resources and risk
# --------------------------------------------------------------------------- #


@router.post("/resources", response_model=ResourceState, summary="Stage 4 — resource assessment")
async def resources(frame: TelemetryFrame, pipeline: PipelineDep) -> ResourceState:
    return pipeline.resource_agent.run(frame)


@router.post("/risk-assessment", response_model=RiskAssessment, summary="Stage 5 — risk assessment")
async def risk(body: RiskRequest, pipeline: PipelineDep) -> RiskAssessment:
    state = pipeline.resource_agent.run(body.frame)
    return await asyncio.to_thread(
        pipeline.risk_agent.run, body.frame, body.detection, body.diagnosis, state, body.recall
    )


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


@router.post("/recovery/generate", response_model=RecoveryPlan, summary="Stage 6 — recovery planning")
async def generate(body: RecoveryGenerateRequest, pipeline: PipelineDep) -> RecoveryPlan:
    state = pipeline.resource_agent.run(body.frame)
    return await asyncio.to_thread(
        pipeline.recovery_agent.run, body.frame, body.diagnosis, body.risk, state, body.recall
    )


# --------------------------------------------------------------------------- #
# Simulate and verify
# --------------------------------------------------------------------------- #


def _what_if(body: ActionRequest, rationale: str) -> tuple[RecoveryOption, Diagnosis]:
    """Resolve the option and diagnosis for a stage call.

    An n8n workflow passes a full `option` and `diagnosis`. The operator what-if
    sandbox passes only an `action_id`; the option is then built from the verified
    action catalogue, so its description, risk tier and subsystem are the
    catalogue's, not guessed.
    """
    action_id = body.option.action_id if body.option else body.action_id
    if action_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide either `option` or `action_id`",
        )
    spec = ACTION_CATALOG.get(action_id)
    if body.option is None and spec is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown action '{action_id}'; known actions: {', '.join(sorted(ACTION_CATALOG))}",
        )
    option = body.option or RecoveryOption(
        action_id=spec.action_id,
        description=spec.description,
        risk_level=spec.risk_level,
        expected_effect=spec.expected_effect,
        rationale=rationale,
    )
    diagnosis = body.diagnosis or Diagnosis(
        subsystem=spec.subsystem if spec else ACTION_CATALOG["enter_safe_mode"].subsystem,
        probable_cause="What-if evaluation",
        confidence=1.0,
    )
    return option, diagnosis


@router.post("/recovery/simulate", response_model=SimulationResult, summary="Stage 7 — digital twin")
async def simulate(body: ActionRequest, pipeline: PipelineDep) -> SimulationResult:
    option, diagnosis = _what_if(body, "Operator what-if sandbox simulation")
    state = pipeline.resource_agent.run(body.frame)
    # Monte Carlo twin runs are CPU-bound; keep them off the event loop.
    return await asyncio.to_thread(pipeline.twin.simulate, body.frame, state, option, diagnosis)


@router.post("/recovery/verify", response_model=SafetyVerdict, summary="Stage 8 — safety verification")
async def verify(body: ActionRequest, pipeline: PipelineDep, simulate_first: bool = True) -> SafetyVerdict:
    """Deterministic verification.

    `simulate_first=true` reproduces the pipeline's own ordering (constraints, then
    twin, then final verdict including SR-005). Set it false to see the
    state-only pre-check in isolation.
    """
    option, diagnosis = _what_if(body, "Operator what-if sandbox verification")
    state = pipeline.resource_agent.run(body.frame)
    simulation = (
        await asyncio.to_thread(pipeline.twin.simulate, body.frame, state, option, diagnosis)
        if simulate_first
        else None
    )
    return pipeline.safety.check(body.frame, state, option, diagnosis, simulation)


# --------------------------------------------------------------------------- #
# Recover
# --------------------------------------------------------------------------- #


@router.post("/recovery/approve", response_model=ApprovalResult, summary="Stage 9 — operator approval")
async def approve(body: ApprovalRequest, pipeline: PipelineDep) -> ApprovalResult:
    """Approve or reject an action the safety engine escalated to a human.

    A `FAIL` verdict never reaches this endpoint: blocked actions are not placed
    in the approval queue, so there is nothing here for an operator to override.
    """
    # Runs in a worker thread: approval waits for any in-flight loop cycle to
    # finish (it may be mid LLM call), and that wait must not stall the event loop.
    return await asyncio.to_thread(
        pipeline.approve,
        anomaly_id=body.anomaly_id,
        action_id=body.action_id,
        approved=body.approved,
        operator=body.operator,
        note=body.note,
    )


@router.get("/recovery/pending", summary="Actions awaiting human approval")
async def pending(pipeline: PipelineDep) -> dict:
    return {"pending": pipeline.pending_approvals()}


# --------------------------------------------------------------------------- #
# Learn
# --------------------------------------------------------------------------- #


@router.post(
    "/mission-memory/learn",
    response_model=MissionLearning,
    summary="Stage 10 — mission learning",
)
async def learn(body: LearnRequest, pipeline: PipelineDep) -> MissionLearning:
    try:
        outcome = Outcome(body.outcome.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown outcome '{body.outcome}'",
        ) from None

    learning = await asyncio.to_thread(
        pipeline.learning_agent.run,
        body.frame,
        body.detection,
        body.diagnosis,
        body.option,
        outcome,
        body.effectiveness,
        None,
        body.recall,
    )
    if body.persist:
        pipeline.memory.record_learning(learning, body.frame.spacecraft_id)
    return learning
