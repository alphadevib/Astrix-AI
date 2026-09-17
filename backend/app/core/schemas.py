"""ASTRIX wire contract.

Every stage of the loop consumes and produces one of these models. Keeping the
whole pipeline in Pydantic means an n8n node, a unit test and the React
dashboard all see identical JSON, and an agent that returns malformed output
fails loudly at the boundary instead of corrupting a downstream stage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .enums import (
    ApprovalStatus,
    ExecutionStatus,
    Outcome,
    ReasonerKind,
    RiskLevel,
    Severity,
    Subsystem,
    VerificationStatus,
    OperatingMode,
)

WHEELS = (1, 2, 3, 4)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AstrixModel(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)


# --------------------------------------------------------------------------- #
# Stage 0 — Telemetry
# --------------------------------------------------------------------------- #


class TelemetryFrame(AstrixModel):
    """One telemetry sample.

    Flat scalar fields on purpose: this shape drops straight into a DataFrame,
    a CSV, a Plotly trace and an n8n expression without any unpacking.
    """

    spacecraft_id: str = "ASTRIX-01"
    mission_id: str = "ASTRIX-M01"
    timestamp: datetime = Field(default_factory=_now)
    seq: int = 0

    # --- context (drives false-alarm suppression, §4.1) ---
    mode: OperatingMode = OperatingMode.NOMINAL
    in_eclipse: bool = False
    sun_angle_deg: float = 0.0
    payload_active: bool = False
    ground_contact: bool = True

    # --- power ---
    battery_voltage: float = 28.0
    battery_current: float = -2.0  # negative = discharging
    state_of_charge: float = 90.0  # percent
    solar_power: float = 220.0  # watts

    # --- thermal ---
    temperature: float = 24.0  # primary bus sensor, °C
    temperature_secondary: float = 23.0  # redundant sensor — disagreement localises faults

    # --- attitude determination & control ---
    gyro_x: float = 0.0
    gyro_y: float = 0.0
    gyro_z: float = 0.0
    wheel_1_rpm: float = 2400.0
    wheel_2_rpm: float = 2400.0
    wheel_3_rpm: float = 2400.0
    wheel_4_rpm: float = 2400.0
    wheel_1_vibration: float = 0.4  # mm/s RMS
    wheel_2_vibration: float = 0.4
    wheel_3_vibration: float = 0.4
    wheel_4_vibration: float = 0.4
    wheel_1_current: float = 0.6  # amps
    wheel_2_current: float = 0.6
    wheel_3_current: float = 0.6
    wheel_4_current: float = 0.6
    attitude_error_deg: float = 0.02
    attitude_q: list[float] = Field(default_factory=lambda: [1.0, 0.0, 0.0, 0.0])

    # --- propulsion ---
    thruster_status: str = "OFF"
    fuel_level: float = 78.0  # percent

    # --- command & data handling ---
    cpu_load: float = 32.0  # percent
    memory_usage: float = 41.0  # percent

    # --- communications ---
    communication_signal: float = 96.0  # percent link quality
    packet_loss: float = 0.1  # percent
    downlink_latency_ms: float = 620.0

    # --- orbit ---
    position_km: list[float] = Field(default_factory=lambda: [6871.0, 0.0, 0.0])
    velocity_kms: list[float] = Field(default_factory=lambda: [0.0, 7.6, 0.0])

    # --- provenance (simulator only; never trusted by the detector) ---
    scenario: str | None = None
    injected_fault: str | None = None

    # -- derived views ------------------------------------------------------ #

    def wheel_rpms(self) -> list[float]:
        return [getattr(self, f"wheel_{n}_rpm") for n in WHEELS]

    def wheel_vibrations(self) -> list[float]:
        return [getattr(self, f"wheel_{n}_vibration") for n in WHEELS]

    def wheel_currents(self) -> list[float]:
        return [getattr(self, f"wheel_{n}_current") for n in WHEELS]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def wheel_vibration(self) -> float:
        """Worst-wheel vibration — the single scalar the master context names."""
        return max(self.wheel_vibrations())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def power_balance(self) -> float:
        """Generation minus consumption, in watts. Negative = draining battery."""
        return self.solar_power - abs(self.battery_current * self.battery_voltage)


class TelemetryIngestResult(AstrixModel):
    accepted: bool
    seq: int
    validation_errors: list[str] = Field(default_factory=list)
    window_size: int = 0


# --------------------------------------------------------------------------- #
# Stage 1 — Detection
# --------------------------------------------------------------------------- #


class ContextFactor(AstrixModel):
    """One term in the context-aware risk score (§18), kept individually
    inspectable so the dashboard can explain *why* a score moved."""

    name: str
    value: float
    weight: float
    note: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contribution(self) -> float:
        return round(self.value * self.weight, 4)


class DetectionResult(AstrixModel):
    spacecraft_id: str
    timestamp: datetime
    ml_score: float = Field(ge=0.0, le=1.0, description="Raw Isolation Forest score, 0..1")
    final_score: float = Field(ge=0.0, le=1.0, description="Context-adjusted risk score")
    severity: Severity
    is_anomaly: bool
    deviating_parameters: list[str] = Field(default_factory=list)
    context_factors: list[ContextFactor] = Field(default_factory=list)
    persistence_seconds: float = 0.0
    suppressed: bool = False
    suppression_reason: str | None = None
    window_size: int = 0
    detector: str = "isolation_forest"


# --------------------------------------------------------------------------- #
# Stage 2 — Memory retrieval
# --------------------------------------------------------------------------- #


class MemoryHit(AstrixModel):
    mission_id: str
    spacecraft_id: str
    title: str
    similarity: float
    subsystem: Subsystem = Subsystem.UNKNOWN
    failure_mode: str | None = None
    recovery_action: str | None = None
    outcome: Outcome = Outcome.UNKNOWN
    excerpt: str = ""
    source: str = "vector"


class MemoryRecall(AstrixModel):
    hits: list[MemoryHit] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    action_success_rates: dict[str, float] = Field(default_factory=dict)
    query: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def best_similarity(self) -> float:
        return max((h.similarity for h in self.hits), default=0.0)


# --------------------------------------------------------------------------- #
# Stage 3 — Diagnosis
# --------------------------------------------------------------------------- #


class Diagnosis(AstrixModel):
    subsystem: Subsystem
    component: str | None = None
    probable_cause: str
    failure_mode: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    historical_match: bool = False
    reasoner: ReasonerKind = ReasonerKind.DETERMINISTIC


# --------------------------------------------------------------------------- #
# Stage 4 — Risk assessment
# --------------------------------------------------------------------------- #


class RiskAssessment(AstrixModel):
    severity: Severity
    mission_impact: str = Field(description="LOW | MEDIUM | HIGH")
    power_risk: str = "LOW"
    attitude_risk: str = "LOW"
    thermal_risk: str = "LOW"
    communication_risk: str = "LOW"
    time_to_impact_minutes: float | None = None
    cascading_risks: list[str] = Field(default_factory=list)
    rationale: str = ""
    reasoner: ReasonerKind = ReasonerKind.DETERMINISTIC


class ResourceState(AstrixModel):
    """What the spacecraft can still afford to spend (§8.6)."""

    state_of_charge: float
    power_margin_w: float
    fuel_level: float
    operational_wheels: list[int] = Field(
        default_factory=list, description="Wheels currently spinning and commandable"
    )
    healthy_wheels: list[int] = Field(
        default_factory=list, description="Operational wheels showing no degradation signature"
    )
    thermal_headroom_c: float = 0.0
    cpu_headroom: float = 0.0
    link_available: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def attitude_control_available(self) -> bool:
        """Three operational wheels is the minimum for 3-axis control.

        Operational, not healthy: a degraded-but-spinning wheel still provides
        torque authority. That distinction is what makes wheel isolation a
        decidable question rather than a guess.
        """
        return len(self.operational_wheels) >= 3


# --------------------------------------------------------------------------- #
# Stage 5 — Recovery planning
# --------------------------------------------------------------------------- #


class RecoveryOption(AstrixModel):
    action_id: str
    description: str
    risk_level: RiskLevel
    rationale: str = ""
    expected_effect: str = ""
    historical_success_rate: float | None = None
    score: float = 0.0
    parameters: dict[str, Any] = Field(default_factory=dict)


class RecoveryPlan(AstrixModel):
    options: list[RecoveryOption] = Field(default_factory=list)
    selected_action_id: str | None = None
    reasoner: ReasonerKind = ReasonerKind.DETERMINISTIC

    @property
    def selected(self) -> RecoveryOption | None:
        if self.selected_action_id is None:
            return None
        return next((o for o in self.options if o.action_id == self.selected_action_id), None)


# --------------------------------------------------------------------------- #
# Stage 6 — Safety verification
# --------------------------------------------------------------------------- #


class RuleResult(AstrixModel):
    rule_id: str
    description: str
    violated: bool
    detail: str = ""


class SafetyVerdict(AstrixModel):
    """Output of the deterministic constraint engine.

    `status == FAIL` is absolute: no LLM output and no operator convenience may
    override it (§9). An operator can approve a YELLOW/RED *passing* plan; they
    cannot approve a failing one.
    """

    action_id: str
    status: VerificationStatus
    effective_risk_level: RiskLevel
    approval: ApprovalStatus
    rules: list[RuleResult] = Field(default_factory=list)
    audit_id: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def violations(self) -> list[str]:
        return [f"{r.rule_id}: {r.detail or r.description}" for r in self.rules if r.violated]


# --------------------------------------------------------------------------- #
# Stage 7 — Simulation
# --------------------------------------------------------------------------- #


class SimulationCheck(AstrixModel):
    name: str
    passed: bool
    value: float
    limit: float
    detail: str = ""


class SimulationResult(AstrixModel):
    action_id: str
    status: VerificationStatus
    horizon_seconds: int
    checks: list[SimulationCheck] = Field(default_factory=list)
    trajectory: dict[str, list[float]] = Field(default_factory=dict)
    summary: str = ""
    effectiveness_estimate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Predicted improvement against the do-nothing baseline. Informational only — "
            "the twin gates on safety, not on effectiveness, so a safe action that does not "
            "fix the fault still passes."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]


# --------------------------------------------------------------------------- #
# Stage 8 — Learning
# --------------------------------------------------------------------------- #


class Lesson(AstrixModel):
    spacecraft_id: str
    category: str
    description: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_status: str = "UNVALIDATED"
    recurring: bool = False


class MissionLearning(AstrixModel):
    mission_id: str
    lessons: list[Lesson] = Field(default_factory=list)
    recovery_effectiveness: Outcome = Outcome.UNKNOWN
    new_limitations: list[str] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    latent_patterns: list[str] = Field(default_factory=list)
    reasoner: ReasonerKind = ReasonerKind.DETERMINISTIC


# --------------------------------------------------------------------------- #
# Spacecraft knowledge profile (§11)
# --------------------------------------------------------------------------- #


class SpacecraftProfile(AstrixModel):
    spacecraft_id: str
    status: str = "GREEN"
    capabilities: dict[str, str] = Field(default_factory=dict)
    known_limitations: list[str] = Field(default_factory=list)
    anomaly_counts: dict[str, int] = Field(default_factory=dict)
    latent_risks: list[str] = Field(default_factory=list)
    successful_recoveries: list[str] = Field(default_factory=list)
    missions_flown: int = 0


# --------------------------------------------------------------------------- #
# The loop envelope
# --------------------------------------------------------------------------- #


class StageTiming(AstrixModel):
    stage: str
    milliseconds: float


class LoopResult(AstrixModel):
    """End-to-end result of one pass of Detect → … → Learn.

    Stages that did not run (because detection cleared the frame, or safety
    blocked the plan) stay `None` — that absence is itself demo-visible signal.
    """

    cycle_id: str
    spacecraft_id: str
    timestamp: datetime
    detection: DetectionResult
    recall: MemoryRecall | None = None
    diagnosis: Diagnosis | None = None
    resources: ResourceState | None = None
    risk: RiskAssessment | None = None
    plan: RecoveryPlan | None = None
    safety: SafetyVerdict | None = None
    simulation: SimulationResult | None = None
    execution_status: ExecutionStatus = ExecutionStatus.NOT_EXECUTED
    anomaly_id: int | None = None
    timings: list[StageTiming] = Field(default_factory=list)
    halted_at: str | None = None
    halt_reason: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_milliseconds(self) -> float:
        return round(sum(t.milliseconds for t in self.timings), 2)


# --------------------------------------------------------------------------- #
# Operator actions
# --------------------------------------------------------------------------- #


class ApprovalRequest(AstrixModel):
    anomaly_id: int
    action_id: str
    approved: bool
    operator: str = "flight-director"
    note: str = ""


class ApprovalResult(AstrixModel):
    anomaly_id: int
    action_id: str
    approval: ApprovalStatus
    execution_status: ExecutionStatus
    message: str = ""
