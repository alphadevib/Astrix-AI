"""Request bodies for the per-stage endpoints.

The pipeline can run end to end in one call (`POST /telemetry`), but n8n drives
the stages individually so the agent workflow is visible during the demo. That
means each stage needs an explicit input contract: its output is a node's output,
and the next node's input.

Stages are stateless with respect to each other — everything a stage needs is in
its body. That is what lets n8n branch, retry or reorder nodes without the
backend holding hidden per-cycle state.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..core.schemas import (
    DetectionResult,
    Diagnosis,
    MemoryRecall,
    RecoveryOption,
    RiskAssessment,
    TelemetryFrame,
)


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DiagnoseRequest(StageRequest):
    frame: TelemetryFrame
    detection: DetectionResult
    recall: MemoryRecall | None = None


class RiskRequest(StageRequest):
    frame: TelemetryFrame
    detection: DetectionResult
    diagnosis: Diagnosis
    recall: MemoryRecall | None = None


class RecoveryGenerateRequest(StageRequest):
    frame: TelemetryFrame
    diagnosis: Diagnosis
    risk: RiskAssessment
    recall: MemoryRecall | None = None


class ActionRequest(StageRequest):
    """Shared by /recovery/verify and /recovery/simulate."""

    frame: TelemetryFrame
    diagnosis: Diagnosis | None = None
    option: RecoveryOption | None = None
    action_id: str | None = None


class LearnRequest(StageRequest):
    frame: TelemetryFrame
    detection: DetectionResult
    diagnosis: Diagnosis
    option: RecoveryOption | None = None
    outcome: str = Field(default="UNKNOWN", description="SUCCESSFUL | PARTIAL | FAILED | UNKNOWN")
    effectiveness: float = Field(default=0.0, ge=0.0, le=1.0)
    recall: MemoryRecall | None = None
    persist: bool = Field(
        default=True, description="Write the extracted lessons into mission memory"
    )


class MissionStartRequest(StageRequest):
    interval: float = Field(default=0.4, gt=0.01, le=10.0, description="Wall-clock seconds per frame")
    dt: float = Field(default=1.0, gt=0.0, le=60.0, description="Simulated seconds per frame")
    scenario: str | None = Field(default=None, description="Fault to inject immediately")
    settle_frames: int = Field(default=30, ge=0, le=600)
    seed: int | None = 42
    include_launch: bool = Field(
        default=True, description="Fly the launch and deployment sequence before orbit operations"
    )
    launch_time_scale: float = Field(
        default=20.0, ge=1.0, le=200.0, description="Simulated launch seconds per wall-clock second"
    )
    launch_fault: str | None = Field(
        default=None, description="Optional launchpad failure mode: premature_meco, ascent_thrust_loss, max_q_excursion"
    )
    vehicle: dict | None = Field(
        default=None, description="Optional Vehicle Studio design (telemetry.vehicles.VehicleDesign) to fly"
    )
