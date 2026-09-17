"""The ASTRIX loop (master context §7).

    Detect -> Diagnose -> Remember -> Plan -> Simulate -> Verify -> Recover -> Learn

One method, `ingest()`, runs the whole thing for one telemetry frame and returns a
`LoopResult` recording what every stage decided — including the stages that did
*not* run, and why. n8n can also drive the stages individually through the API;
this class is the reference implementation of the ordering those workflows must
preserve.

Three behaviours here are worth reading closely, because they are where the
architecture's claims are either honoured or quietly broken:

**Options are tried in order until one verifies.** The Recovery Agent's top pick
is not privileged. If the safety engine blocks it, the loop moves to the next
ranked option and records the rejection. "AI proposes, constraints verify" means
the constraints get to say no more than once.

**Execution is a command the spacecraft fetches, not a function call.** Approved
actions are queued and returned in the telemetry response, so the simulator
applies them on its next step. The loop never reaches into the vehicle.

**Outcomes are measured, not predicted.** After execution the loop watches the
next `outcome_evaluation_frames` frames and compares the fault indicator against
what it was. Only then does the Learning Agent run, with a real outcome. The
twin's `effectiveness_estimate` is a prediction and is never recorded as a result.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from time import perf_counter

from ..agents.critic import CriticAgent
from ..agents.diagnostic import DiagnosticAgent
from ..agents.learning import LearningAgent
from ..agents.llm import AgentLLM
from ..agents.recovery import RecoveryAgent
from ..agents.resource import ResourceAgent
from ..agents.risk import RiskAgent
from ..memory.knowledge_graph import SpacecraftKnowledgeGraph
from ..config import Settings
from ..core.enums import (
    ApprovalStatus,
    ExecutionStatus,
    Outcome,
    Severity,
    Subsystem,
    VerificationStatus,
)
from ..core.schemas import (
    ApprovalResult,
    Diagnosis,
    DetectionResult,
    LoopResult,
    MemoryRecall,
    MissionLearning,
    RecoveryOption,
    ResourceState,
    RuleResult,
    SafetyVerdict,
    SimulationResult,
    StageTiming,
    TelemetryFrame,
)
from ..memory.store import MissionMemory
from ..ml.context import ContextScorer, implicated_subsystem
from ..ml.detector import AnomalyDetector
from ..safety.engine import SafetyEngine
from ..simulation.twin import DigitalTwin
from .buffer import TelemetryBuffer
from .bus import EventBus

log = logging.getLogger(__name__)

# How many ranked options the loop will try before giving up on this cycle.
MAX_VERIFICATION_ATTEMPTS = 4

# Consecutive nominal frames before an open anomaly is considered over. One clean
# frame in the middle of a developing fault means nothing.
CLEAR_AFTER_NOMINAL_FRAMES = 15

# After a recovery is measured as FAILED, the same action is not proposed again for
# this many frames. Without this, a persisting fault re-opens an anomaly the frame
# after evaluation and the loop re-executes the action that just failed, forever.
FAILED_ACTION_COOLDOWN_FRAMES = 600

# Severity at which the loop starts planning a recovery. Below this an anomaly is
# diagnosed and tracked but no action is proposed — that is what WATCH means.
PLANNING_SEVERITY = Severity.WARNING


@dataclass
class _ActiveAnomaly:
    """An anomaly event being tracked across frames."""

    anomaly_id: int
    subsystem: Subsystem
    severity_at_last_run: Severity
    opened_seq: int
    suppressed: bool = False
    nominal_streak: int = 0


@dataclass
class _PendingOutcome:
    """A recovery awaiting real-world verdict."""

    anomaly_id: int
    action_row_id: int
    option: RecoveryOption
    diagnosis: Diagnosis
    detection: DetectionResult
    simulation: SimulationResult | None
    recall: MemoryRecall | None
    baseline_indicator: float
    evaluate_at_seq: int
    spacecraft_id: str


@dataclass
class _Command:
    action_id: str
    anomaly_id: int
    reason: str
    issued: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class _Timer:
    """Per-stage timing, so the demo can show where the latency actually goes."""

    def __init__(self) -> None:
        self.timings: list[StageTiming] = []

    def stage(self, name: str):
        return _StageTimer(self, name)


class _StageTimer:
    def __init__(self, timer: _Timer, name: str) -> None:
        self.timer = timer
        self.name = name

    def __enter__(self) -> "_StageTimer":
        self.start = perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        elapsed = (perf_counter() - self.start) * 1000.0
        self.timer.timings.append(StageTiming(stage=self.name, milliseconds=round(elapsed, 2)))


class AstrixPipeline:
    def __init__(
        self,
        settings: Settings,
        detector: AnomalyDetector,
        scorer: ContextScorer,
        memory: MissionMemory,
        safety: SafetyEngine,
        twin: DigitalTwin,
        llm: AgentLLM,
        buffer: TelemetryBuffer,
        bus: EventBus,
    ) -> None:
        self.s = settings
        self.detector = detector
        self.scorer = scorer
        self.memory = memory
        self.safety = safety
        self.twin = twin
        self.llm = llm
        self.buffer = buffer
        self.bus = bus

        self.diagnostic_agent = DiagnosticAgent(llm)
        self.resource_agent = ResourceAgent()
        self.risk_agent = RiskAgent(llm)
        self.recovery_agent = RecoveryAgent(llm)
        self.learning_agent = LearningAgent(llm)
        self.critic_agent = CriticAgent(getattr(llm, "gateway", None))
        self.knowledge_graph = SpacecraftKnowledgeGraph()

        self._pending: dict[str, _PendingOutcome] = {}
        self._commands: dict[str, list[_Command]] = {}
        self._awaiting_approval: dict[int, _PendingOutcome] = {}
        self._active: dict[str, _ActiveAnomaly] = {}
        self._last_published_severity: dict[str, Severity] = {}
        self._ml_ema: dict[str, float] = {}
        self._severity_state: dict[str, Severity] = {}
        self._last_seq: dict[str, int] = {}
        self._failed_actions: dict[str, dict[str, int]] = {}
        # The runner steps the loop in a worker thread while operator approvals
        # arrive on API requests. Every read-modify-write of loop state happens
        # under this lock, so an approval can never interleave with a cycle that
        # is re-planning the same anomaly.
        self._lock = threading.RLock()
        self.cycles = 0
        self.anomalies_opened = 0
        self.suppressed_count = 0

    # ===================================================================== #
    # Main loop
    # ===================================================================== #

    def ingest(self, frame: TelemetryFrame) -> LoopResult:
        with self._lock:
            last_seq = self._last_seq.get(frame.spacecraft_id)
            if last_seq is not None and frame.seq <= last_seq:
                # The sequence counter went backwards: a new telemetry session
                # (simulator restart, spacecraft reboot). Anomaly tracking, smoothing
                # state, pending outcomes and the rolling window from the old session
                # must not bleed into the new one.
                log.warning(
                    "%s telemetry seq reset (%d -> %d); starting a new session",
                    frame.spacecraft_id,
                    last_seq,
                    frame.seq,
                )
                self.reset(frame.spacecraft_id, reason="telemetry sequence restarted")
            self._last_seq[frame.spacecraft_id] = frame.seq
            return self._ingest(frame)

    def reset(self, spacecraft_id: str | None = None, reason: str = "mission reset") -> None:
        """Forget all per-spacecraft loop state. Mission memory is kept."""
        with self._lock:
            if spacecraft_id is not None:
                crafts = [spacecraft_id]
            else:
                crafts = list(
                    set(self._active) | set(self._pending) | set(self._last_seq) | set(self._ml_ema)
                    | {p.spacecraft_id for p in self._awaiting_approval.values()}
                )
            for craft in crafts:
                for anomaly_id, pending in list(self._awaiting_approval.items()):
                    if pending.spacecraft_id == craft:
                        self._expire_approval(anomaly_id, reason)
                self._active.pop(craft, None)
                self._pending.pop(craft, None)
                self._commands.pop(craft, None)
                self._last_published_severity.pop(craft, None)
                self._ml_ema.pop(craft, None)
                self._severity_state.pop(craft, None)
                self._last_seq.pop(craft, None)
                self._failed_actions.pop(craft, None)
                self.buffer.clear(craft)

    def _ingest(self, frame: TelemetryFrame) -> LoopResult:
        timer = _Timer()
        cycle_id = uuid.uuid4().hex[:12]
        self.cycles += 1

        # ---------------- Stage 0: telemetry ---------------- #
        with timer.stage("telemetry"):
            errors = self.buffer.validate(frame)
            window_size = self.buffer.add(frame)
            self.memory.record_frame(frame)
            if errors:
                log.warning("telemetry validation: %s", "; ".join(errors))
        self.bus.publish("telemetry", frame.model_dump(mode="json"))

        # Score any recovery that has been waiting for a real outcome.
        self._evaluate_pending(frame)

        # ---------------- Stage 1: detection ---------------- #
        with timer.stage("detection"):
            detection, recall = self._detect(frame, window_size)

        # Detection runs on every frame, but a developing fault holds one severity
        # for hundreds of frames. Publishing each result would push thousands of
        # identical events through the socket and flush the activity feed's replay
        # buffer, burying the diagnosis and approval events an operator needs.
        # A severity *change* is the event; a severity holding steady is not.
        previous = self._last_published_severity.get(frame.spacecraft_id)
        if detection.severity is not previous:
            self.bus.publish("detection", detection.model_dump(mode="json"))
        self._last_published_severity[frame.spacecraft_id] = detection.severity

        result = LoopResult(
            cycle_id=cycle_id,
            spacecraft_id=frame.spacecraft_id,
            timestamp=frame.timestamp,
            detection=detection,
            timings=timer.timings,
        )

        active = self._active.get(frame.spacecraft_id)

        if not detection.is_anomaly:
            # An event is only over once telemetry has been clean for a while; a
            # single good frame mid-fault must not reset the investigation.
            if active is not None:
                active.nominal_streak += 1
                if active.nominal_streak >= CLEAR_AFTER_NOMINAL_FRAMES:
                    self._close_active(frame.spacecraft_id, "telemetry returned to nominal")
            result.halted_at = "detection"
            result.halt_reason = "telemetry nominal — no anomaly to diagnose"
            return result  # no `cycle` event: nominal frames would flood the feed

        subsystem = implicated_subsystem(detection.deviating_parameters)
        result.recall = recall
        if active is not None:
            active.nominal_streak = 0

        # ------------------------------------------------------------------ #
        # Event tracking.
        #
        # A developing fault produces hundreds of anomalous frames. Opening a new
        # anomaly and re-running the whole agent chain on each one would flood
        # mission memory with duplicates and fire an LLM call per frame. So an
        # anomaly is an *event*, opened once and then tracked: the chain re-runs
        # only when the situation materially changes, i.e. severity escalates.
        # ------------------------------------------------------------------ #
        if active is not None and active.suppressed and not detection.suppressed:
            # A deviation we had written off as benign has become corroborated.
            self._close_active(frame.spacecraft_id, "deviation escalated beyond suppression")
            active = None

        if detection.suppressed:
            if active is not None:
                result.anomaly_id = active.anomaly_id
                result.halted_at = "detection"
                result.halt_reason = detection.suppression_reason
                return self._finish(result, timer)

            # Recorded once per streak so the false-alarm reduction is auditable.
            self.suppressed_count += 1
            anomaly_id = self.memory.open_anomaly(
                frame, detection, subsystem, "suppressed_deviation"
            )
            self._active[frame.spacecraft_id] = _ActiveAnomaly(
                anomaly_id=anomaly_id,
                subsystem=subsystem,
                severity_at_last_run=detection.severity,
                opened_seq=frame.seq,
                suppressed=True,
            )
            result.anomaly_id = anomaly_id
            result.halted_at = "detection"
            result.halt_reason = detection.suppression_reason
            self.memory.audit(
                audit_id=cycle_id,
                cycle_id=cycle_id,
                stage="suppression",
                detail={"anomaly_id": anomaly_id, "reason": detection.suppression_reason},
            )
            self.bus.publish(
                "suppressed",
                {"anomaly_id": anomaly_id, "reason": detection.suppression_reason},
            )
            return self._finish(result, timer)

        pending_outcome = self._pending.get(frame.spacecraft_id)
        if pending_outcome is not None and active is not None:
            # A recovery is already executing. Planning a second action now would
            # overwrite the first one's outcome measurement and stack commands the
            # spacecraft is still responding to. Wait for the verdict.
            result.anomaly_id = active.anomaly_id
            result.halted_at = "recovery"
            result.halt_reason = (
                f"'{pending_outcome.option.action_id}' is executing; outcome measured at "
                f"frame {pending_outcome.evaluate_at_seq}"
            )
            return result

        if active is None:
            self.anomalies_opened += 1
            anomaly_id = self.memory.open_anomaly(frame, detection, subsystem, "under_diagnosis")
            active = _ActiveAnomaly(
                anomaly_id=anomaly_id,
                subsystem=subsystem,
                severity_at_last_run=detection.severity,
                opened_seq=frame.seq,
            )
            self._active[frame.spacecraft_id] = active
        else:
            anomaly_id = active.anomaly_id
            escalated = detection.severity.rank > active.severity_at_last_run.rank
            if not escalated:
                result.anomaly_id = anomaly_id
                result.halted_at = "tracking"
                result.halt_reason = (
                    f"anomaly {anomaly_id} already open at {active.severity_at_last_run.value}; "
                    f"monitoring for escalation"
                )
                # No `cycle` event: nothing was decided, and republishing the same
                # state every frame would bury the cycle that did decide something.
                return result
            log.info(
                "anomaly %d escalated %s -> %s; re-running the agent chain",
                anomaly_id,
                active.severity_at_last_run.value,
                detection.severity.value,
            )
            active.severity_at_last_run = detection.severity

        result.anomaly_id = anomaly_id

        # ---------------- Stage 2: diagnosis & multi-agent debate ---------------- #
        with timer.stage("diagnosis"):
            diagnosis = self.diagnostic_agent.run(frame, detection, recall)
            self.memory.attach_diagnosis(anomaly_id, diagnosis)
        result.diagnosis = diagnosis
        self.bus.publish("diagnosis", {"anomaly_id": anomaly_id, **diagnosis.model_dump(mode="json")})

        # Multi-Agent Debate & Devil's Advocate Critic Review
        with timer.stage("critic_debate"):
            channels_with_scores = [
                s.channel for s in getattr(detection, "scored_channels", []) if getattr(s, "anomaly_score", 0.0) > 0.25
            ]
            kg_explanation = self.knowledge_graph.explain_anomaly(
                diagnosis.subsystem.value,
                channels_with_scores,
            )
            critic_review = self.critic_agent.review(frame, diagnosis)
            self.bus.publish(
                "critic_review",
                {"anomaly_id": anomaly_id, **critic_review.model_dump(mode="json")},
            )
            self.bus.publish(
                "agent_thought",
                {
                    "anomaly_id": anomaly_id,
                    "stage": "critic_debate",
                    "subsystem": diagnosis.subsystem.value,
                    "diagnosis": diagnosis.probable_cause,
                    "confidence": critic_review.confidence_score,
                    "confirmation_bias_detected": critic_review.confirmation_bias_detected,
                    "sensor_spoofing_risk": critic_review.sensor_spoofing_risk,
                    "consensus": critic_review.consensus_recommendation,
                    "counter_arguments": critic_review.counter_arguments,
                    "ruled_out": [h.model_dump() for h in critic_review.ruled_out_hypotheses],
                    "knowledge_graph_explanation": kg_explanation,
                },
            )

        # ---------------- Stage 3: resources ---------------- #
        with timer.stage("resources"):
            resources = self.resource_agent.run(frame)
        result.resources = resources

        # ---------------- Stage 4: risk ---------------- #
        with timer.stage("risk"):
            risk = self.risk_agent.run(frame, detection, diagnosis, resources, recall)
            self.memory.attach_risk(anomaly_id, risk)
        result.risk = risk
        self.bus.publish("risk", {"anomaly_id": anomaly_id, **risk.model_dump(mode="json")})

        # WATCH means "keep an eye on this", not "act". The anomaly is open,
        # diagnosed and visible to the operator, but proposing a spacecraft
        # reconfiguration on a weak, possibly-transient signal is how automation
        # loses an operator's trust.
        if detection.severity.rank < PLANNING_SEVERITY.rank:
            result.halted_at = "planning"
            result.halt_reason = (
                f"{detection.severity.value} severity: diagnosed and monitored; recovery "
                f"planning begins at {PLANNING_SEVERITY.value}"
            )
            return self._finish(result, timer)

        # ---------------- Stage 5: planning ---------------- #
        with timer.stage("planning"):
            plan = self.recovery_agent.run(frame, diagnosis, risk, resources, recall)
        result.plan = plan
        self.bus.publish("plan", {"anomaly_id": anomaly_id, **plan.model_dump(mode="json")})

        if not plan.options:
            result.halted_at = "planning"
            result.halt_reason = "no candidate recovery action available for this diagnosis"
            return self._finish(result, timer)

        # ---------------- Stages 6 & 7: verify, simulate, verify ---------------- #
        with timer.stage("verification"):
            verified = self._verify_options(
                cycle_id, anomaly_id, frame, resources, diagnosis, plan
            )
        option, safety, simulation = verified
        result.safety = safety
        result.simulation = simulation
        if simulation is not None:
            self.bus.publish(
                "simulation", {"anomaly_id": anomaly_id, **simulation.model_dump(mode="json")}
            )
        self.bus.publish("safety", {"anomaly_id": anomaly_id, **safety.model_dump(mode="json")})

        if option is None or safety.status is VerificationStatus.FAIL:
            result.halted_at = "verification"
            result.halt_reason = (
                "every candidate action was rejected: " + "; ".join(safety.violations)
            )
            return self._finish(result, timer)

        # The verified option replaces whatever the agent originally selected.
        plan.selected_action_id = option.action_id
        action_row_id = self.memory.record_action(anomaly_id, option, plan, safety, simulation)

        pending = _PendingOutcome(
            anomaly_id=anomaly_id,
            action_row_id=action_row_id,
            option=option,
            diagnosis=diagnosis,
            detection=detection,
            simulation=simulation,
            recall=recall,
            baseline_indicator=self._indicator(frame, diagnosis.subsystem),
            evaluate_at_seq=frame.seq + self.s.outcome_evaluation_frames,
            spacecraft_id=frame.spacecraft_id,
        )

        # ---------------- Stage 8: execute or escalate ---------------- #
        if safety.approval is ApprovalStatus.AUTO_APPROVED:
            self._execute(pending, operator="astrix-autonomous")
            result.execution_status = ExecutionStatus.EXECUTED
        else:
            if anomaly_id in self._awaiting_approval:
                self._expire_approval(anomaly_id, "superseded by a new plan after escalation")
            self._awaiting_approval[anomaly_id] = pending
            result.execution_status = ExecutionStatus.SIMULATED
            self.bus.publish(
                "approval_required",
                {
                    "anomaly_id": anomaly_id,
                    "action_id": option.action_id,
                    "description": option.description,
                    "risk_level": safety.effective_risk_level.value,
                    "simulation_status": simulation.status.value if simulation else "NOT_RUN",
                    "rationale": option.rationale,
                },
            )

        self.memory.audit(
            audit_id=safety.audit_id,
            cycle_id=cycle_id,
            stage="decision",
            detail={
                "anomaly_id": anomaly_id,
                "action_id": option.action_id,
                "risk_level": safety.effective_risk_level.value,
                "approval": safety.approval.value,
                "simulation": simulation.status.value if simulation else "NOT_RUN",
                "reasoner": plan.reasoner.value,
            },
        )
        return self._finish(result, timer)

    def _close_active(self, spacecraft_id: str, reason: str) -> None:
        active = self._active.pop(spacecraft_id, None)
        if active is None:
            return
        if active.anomaly_id in self._awaiting_approval:
            # Approving a plan for an anomaly that no longer exists would command
            # the spacecraft to fix a problem it does not have.
            self._expire_approval(active.anomaly_id, f"anomaly closed: {reason}")
        log.info("anomaly %d closed: %s", active.anomaly_id, reason)
        self.bus.publish(
            "anomaly_closed", {"anomaly_id": active.anomaly_id, "reason": reason}
        )

    def _expire_approval(self, anomaly_id: int, reason: str) -> None:
        pending = self._awaiting_approval.pop(anomaly_id, None)
        if pending is None:
            return
        self.memory.set_action_status(
            pending.action_row_id,
            approval_status=ApprovalStatus.REJECTED.value,
            operator="astrix-expired",
        )
        log.info(
            "approval for '%s' on anomaly %d expired: %s", pending.option.action_id, anomaly_id, reason
        )
        self.bus.publish(
            "approval_expired",
            {"anomaly_id": anomaly_id, "action_id": pending.option.action_id, "reason": reason},
        )

    def has_pending_outcome(self, spacecraft_id: str | None = None) -> bool:
        """True while a recovery is still being measured."""
        if spacecraft_id is None:
            return bool(self._pending)
        return spacecraft_id in self._pending

    def _finish(self, result: LoopResult, timer: "_Timer | None" = None) -> LoopResult:
        """Publish the whole cycle as one event.

        The granular per-stage events drive the activity feed; this consolidated
        event gives the dashboard a single consistent snapshot, so it never renders
        a diagnosis from one cycle beside a simulation from the next.
        """
        if timer is not None:
            # Pydantic copied the list when the result was constructed, so the
            # stage timings recorded since then have to be re-attached here.
            result.timings = list(timer.timings)
        self.bus.publish("cycle", result.model_dump(mode="json"))
        return result

    # ===================================================================== #
    # Stage implementations
    # ===================================================================== #

    def _detect(
        self, frame: TelemetryFrame, window_size: int
    ) -> tuple[DetectionResult, MemoryRecall | None]:
        output = self.detector.score(frame)

        if not output.fitted:
            # No model yet: report honestly rather than emitting a fake score.
            return (
                DetectionResult(
                    spacecraft_id=frame.spacecraft_id,
                    timestamp=frame.timestamp,
                    ml_score=0.0,
                    final_score=0.0,
                    severity=Severity.NORMAL,
                    is_anomaly=False,
                    window_size=window_size,
                    detector="untrained",
                ),
                None,
            )

        # Smooth the raw detector score before anything downstream sees it. The
        # Isolation Forest scores one sample at a time, so its output carries the
        # frame's sensor noise directly; an unsmoothed score crosses severity
        # thresholds on noise alone. Smoothing the *input* keeps every number in
        # the published score breakdown consistent with the final score.
        previous_ema = self._ml_ema.get(frame.spacecraft_id)
        alpha = self.s.ml_smoothing_alpha
        ml_score = (
            output.score
            if previous_ema is None
            else alpha * output.score + (1.0 - alpha) * previous_ema
        )
        self._ml_ema[frame.spacecraft_id] = ml_score

        persistence = self.buffer.update_persistence(
            frame.spacecraft_id, frame, ml_score >= self.s.persistence_ml_threshold
        )

        # Memory is only queried once the frame is interesting enough to be worth
        # the lookup — see `recall_ml_threshold`.
        recall: MemoryRecall | None = None
        if ml_score >= self.s.recall_ml_threshold:
            recall = self.memory.recall(
                query=self._recall_query(frame, output.deviating_parameters),
                subsystem=implicated_subsystem(output.deviating_parameters),
                k=self.s.vector_top_k,
                min_similarity=self.s.vector_min_similarity,
            )

        detection = self.scorer.score(
            frame=frame,
            ml_score=ml_score,
            deviating_parameters=output.deviating_parameters,
            recall=recall,
            persistence_seconds=persistence,
            window_size=window_size,
            previous_severity=self._severity_state.get(frame.spacecraft_id),
        )
        self._severity_state[frame.spacecraft_id] = detection.severity
        return detection, recall

    def _recall_query(self, frame: TelemetryFrame, deviating: list[str]) -> str:
        """Build the retrieval query from the deviating channels themselves.

        Querying on channel names plus the implicated subsystem is what makes the
        lexical embedder work well here: mission write-ups discuss faults in the
        same vocabulary the telemetry uses.
        """
        subsystem = implicated_subsystem(deviating)
        terms = [subsystem.value, frame.mode.value]
        terms += [param.replace("_", " ") for param in deviating[:8]]
        if max(frame.wheel_vibrations()) > 1.0:
            terms += ["reaction wheel vibration motor current degradation"]
        if frame.state_of_charge < 50.0 or frame.battery_voltage < 27.0:
            terms += ["battery capacity degradation state of charge discharge"]
        if frame.temperature > 45.0:
            terms += ["temperature thermal excursion compute load"]
        if frame.packet_loss > 2.0:
            terms += ["communication link packet loss signal degradation"]
        return " ".join(terms)

    def _verify_options(
        self,
        cycle_id: str,
        anomaly_id: int,
        frame: TelemetryFrame,
        resources: ResourceState,
        diagnosis: Diagnosis,
        plan,
    ) -> tuple[RecoveryOption | None, SafetyVerdict, SimulationResult | None]:
        """Try ranked options until one clears constraints *and* simulation."""
        last_verdict: SafetyVerdict | None = None
        last_simulation: SimulationResult | None = None

        failed = self._failed_actions.get(frame.spacecraft_id, {})
        candidates = [
            o
            for o in plan.options
            if o.action_id not in failed
            or frame.seq - failed[o.action_id] > FAILED_ACTION_COOLDOWN_FRAMES
        ]
        skipped = [o.action_id for o in plan.options if o not in candidates]
        if skipped:
            self.memory.audit(
                audit_id=uuid.uuid4().hex[:12],
                cycle_id=cycle_id,
                stage="skipped_recently_failed",
                detail={"anomaly_id": anomaly_id, "actions": skipped},
            )
        if not candidates:
            verdict = self.safety.check(frame, resources, plan.options[0], diagnosis)
            verdict.rules.append(
                RuleResult(
                    rule_id="LOOP-001",
                    description="Do not repeat a recovery that just failed",
                    violated=True,
                    detail=(
                        "every candidate action failed within the last "
                        f"{FAILED_ACTION_COOLDOWN_FRAMES} frames; operator attention required"
                    ),
                )
            )
            verdict.status = VerificationStatus.FAIL
            verdict.approval = ApprovalStatus.BLOCKED
            return None, verdict, None

        for option in candidates[:MAX_VERIFICATION_ATTEMPTS]:
            # Pre-simulation constraint check: state-based rules only.
            precheck = self.safety.check(frame, resources, option, diagnosis)
            if precheck.status is VerificationStatus.FAIL:
                self.memory.audit(
                    audit_id=precheck.audit_id,
                    cycle_id=cycle_id,
                    stage="safety_precheck",
                    detail={
                        "anomaly_id": anomaly_id,
                        "action_id": option.action_id,
                        "rejected": precheck.violations,
                    },
                )
                last_verdict, last_simulation = precheck, None
                continue

            simulation = self.twin.simulate(frame, resources, option, diagnosis)
            # Final verification, now including the twin verdict (rule SR-005).
            verdict = self.safety.check(
                frame, resources, option, diagnosis, simulation, audit_id=precheck.audit_id
            )
            if verdict.status is VerificationStatus.PASS:
                return option, verdict, simulation

            self.memory.audit(
                audit_id=verdict.audit_id,
                cycle_id=cycle_id,
                stage="verification_rejected",
                detail={
                    "anomaly_id": anomaly_id,
                    "action_id": option.action_id,
                    "rejected": verdict.violations,
                    "failed_checks": simulation.failed_checks,
                },
            )
            last_verdict, last_simulation = verdict, simulation

        assert last_verdict is not None  # plan.options is non-empty at the call site
        return None, last_verdict, last_simulation

    # ===================================================================== #
    # Execution and outcome measurement
    # ===================================================================== #

    def _execute(self, pending: _PendingOutcome, operator: str) -> None:
        """Queue the action for the spacecraft and start measuring its outcome."""
        # Measure from *now*, not from when the plan was made. An operator may
        # approve minutes after the decision; the decision-time baseline would
        # credit or blame the recovery for the fault's growth in between, and a
        # stale evaluate_at_seq would judge the action before it had any effect.
        latest = self.buffer.latest(pending.spacecraft_id)
        if latest is not None:
            pending.baseline_indicator = self._indicator(latest, pending.diagnosis.subsystem)
            pending.evaluate_at_seq = latest.seq + self.s.outcome_evaluation_frames
        self._commands.setdefault(pending.spacecraft_id, []).append(
            _Command(
                action_id=pending.option.action_id,
                anomaly_id=pending.anomaly_id,
                reason=pending.diagnosis.probable_cause,
            )
        )
        self.memory.set_action_status(
            pending.action_row_id,
            execution_status=ExecutionStatus.EXECUTED.value,
            operator=operator,
        )
        self.buffer.reset_persistence(pending.spacecraft_id)
        self._pending[pending.spacecraft_id] = pending
        self.bus.publish(
            "executed",
            {
                "anomaly_id": pending.anomaly_id,
                "action_id": pending.option.action_id,
                "operator": operator,
                "evaluating_for_frames": self.s.outcome_evaluation_frames,
            },
        )

    def pop_commands(self, spacecraft_id: str) -> list[dict]:
        """Commands the spacecraft should apply, drained on read."""
        with self._lock:
            queued = self._commands.pop(spacecraft_id, [])
        return [
            {"action_id": c.action_id, "anomaly_id": c.anomaly_id, "reason": c.reason, "id": c.issued}
            for c in queued
        ]

    def _indicator(self, frame: TelemetryFrame, subsystem: Subsystem) -> float:
        """Scalar health indicator for a subsystem: higher is worse.

        Comparing this before and after a recovery is how the loop measures a real
        outcome instead of trusting the twin's prediction.
        """
        if subsystem is Subsystem.ADCS:
            return frame.attitude_error_deg + 0.5 * max(frame.wheel_vibrations())
        if subsystem is Subsystem.THERMAL:
            return max(0.0, frame.temperature - 25.0)
        if subsystem is Subsystem.POWER:
            return max(0.0, 90.0 - frame.state_of_charge) + max(0.0, -frame.power_balance) / 10.0
        if subsystem is Subsystem.COMMS:
            return frame.packet_loss + max(0.0, 96.0 - frame.communication_signal)
        if subsystem is Subsystem.CDH:
            return max(0.0, frame.cpu_load - 50.0) + max(0.0, frame.memory_usage - 50.0)
        return frame.attitude_error_deg

    def _evaluate_pending(self, frame: TelemetryFrame) -> None:
        pending = self._pending.get(frame.spacecraft_id)
        if pending is None or frame.seq < pending.evaluate_at_seq:
            return
        self._pending.pop(frame.spacecraft_id, None)

        before = pending.baseline_indicator
        after = self._indicator(frame, pending.diagnosis.subsystem)
        if before <= 1e-6:
            improvement = 1.0 if after <= before + 1e-6 else 0.0
        else:
            improvement = (before - after) / before
        improvement = float(min(1.0, max(0.0, improvement)))

        if improvement >= 0.7:
            outcome = Outcome.SUCCESSFUL
        elif improvement >= 0.3:
            outcome = Outcome.PARTIAL
        else:
            outcome = Outcome.FAILED
            self._failed_actions.setdefault(frame.spacecraft_id, {})[
                pending.option.action_id
            ] = frame.seq

        side_effects: list[str] = []
        if frame.mode.value == "SAFE_MODE":
            side_effects.append("spacecraft is in safe mode; mission operations suspended")
        if len(self.resource_agent.run(frame).operational_wheels) < 4:
            side_effects.append("operating with reduced reaction wheel redundancy")

        self.memory.record_outcome(
            action_row_id=pending.action_row_id,
            outcome=outcome,
            effectiveness=round(improvement, 3),
            side_effects=side_effects,
            notes=(
                f"Measured over {self.s.outcome_evaluation_frames} frames: "
                f"{pending.diagnosis.subsystem.value} health indicator {before:.3f} -> {after:.3f}. "
                f"Twin had predicted {pending.simulation.effectiveness_estimate:.2f}."
                if pending.simulation
                else f"Indicator {before:.3f} -> {after:.3f}."
            ),
        )
        self.bus.publish(
            "outcome",
            {
                "anomaly_id": pending.anomaly_id,
                "action_id": pending.option.action_id,
                "outcome": outcome.value,
                "effectiveness": round(improvement, 3),
                "indicator_before": round(before, 4),
                "indicator_after": round(after, 4),
                "predicted": pending.simulation.effectiveness_estimate if pending.simulation else None,
                "side_effects": side_effects,
            },
        )

        # ---------------- Stage 9: learning ---------------- #
        learning = self.learning_agent.run(
            frame=frame,
            detection=pending.detection,
            diagnosis=pending.diagnosis,
            executed=pending.option,
            outcome=outcome,
            effectiveness=improvement,
            simulation=pending.simulation,
            recall=pending.recall,
        )
        # The event is closed out: a later fault opens a fresh anomaly rather than
        # attaching to this resolved one.
        self._close_active(frame.spacecraft_id, f"recovery evaluated: {outcome.value}")

        lesson_ids = self.memory.record_learning(learning, frame.spacecraft_id)
        self.bus.publish(
            "learning",
            {
                "anomaly_id": pending.anomaly_id,
                "lesson_ids": lesson_ids,
                **learning.model_dump(mode="json"),
            },
        )
        log.info(
            "mission memory updated: %d lesson(s) from anomaly %d (outcome %s)",
            len(lesson_ids),
            pending.anomaly_id,
            outcome.value,
        )

    # ===================================================================== #
    # Operator actions
    # ===================================================================== #

    def approve(
        self, anomaly_id: int, action_id: str, approved: bool, operator: str, note: str = ""
    ) -> ApprovalResult:
        with self._lock:
            return self._approve(anomaly_id, action_id, approved, operator, note)

    def _approve(
        self, anomaly_id: int, action_id: str, approved: bool, operator: str, note: str
    ) -> ApprovalResult:
        pending = self._awaiting_approval.get(anomaly_id)
        if pending is None:
            return ApprovalResult(
                anomaly_id=anomaly_id,
                action_id=action_id,
                approval=ApprovalStatus.REJECTED,
                execution_status=ExecutionStatus.NOT_EXECUTED,
                message=f"no action is awaiting approval for anomaly {anomaly_id}",
            )
        if pending.option.action_id != action_id:
            return ApprovalResult(
                anomaly_id=anomaly_id,
                action_id=action_id,
                approval=ApprovalStatus.REJECTED,
                execution_status=ExecutionStatus.NOT_EXECUTED,
                message=(
                    f"anomaly {anomaly_id} is awaiting approval for "
                    f"'{pending.option.action_id}', not '{action_id}'"
                ),
            )

        self._awaiting_approval.pop(anomaly_id, None)

        if not approved:
            self.memory.set_action_status(
                pending.action_row_id,
                approval_status=ApprovalStatus.REJECTED.value,
                operator=operator,
            )
            self.memory.audit(
                audit_id=uuid.uuid4().hex[:12],
                cycle_id=f"approval-{anomaly_id}",
                stage="approval",
                actor=operator,
                detail={"anomaly_id": anomaly_id, "action_id": action_id, "approved": False, "note": note},
            )
            self.bus.publish(
                "approval", {"anomaly_id": anomaly_id, "action_id": action_id, "approved": False}
            )
            return ApprovalResult(
                anomaly_id=anomaly_id,
                action_id=action_id,
                approval=ApprovalStatus.REJECTED,
                execution_status=ExecutionStatus.NOT_EXECUTED,
                message="action rejected by operator; no command issued",
            )

        # The plan was verified against the spacecraft as it was when the plan was
        # made. Re-check the safety rules against the latest telemetry: an operator's
        # approval must never push an action the constraints would now block (e.g.
        # the battery has since fallen below the floor the action needs).
        latest = self.buffer.latest(pending.spacecraft_id)
        if latest is not None:
            recheck = self.safety.check(
                latest,
                self.resource_agent.run(latest),
                pending.option,
                pending.diagnosis,
                pending.simulation,
            )
            if recheck.status is VerificationStatus.FAIL:
                self.memory.set_action_status(
                    pending.action_row_id,
                    approval_status=ApprovalStatus.BLOCKED.value,
                    operator=operator,
                )
                self.memory.audit(
                    audit_id=recheck.audit_id,
                    cycle_id=f"approval-{anomaly_id}",
                    stage="approval_reverification_failed",
                    actor=operator,
                    detail={
                        "anomaly_id": anomaly_id,
                        "action_id": action_id,
                        "violations": recheck.violations,
                    },
                )
                reason = "blocked on re-verification: " + "; ".join(recheck.violations)
                self.bus.publish(
                    "approval_expired",
                    {"anomaly_id": anomaly_id, "action_id": action_id, "reason": reason},
                )
                return ApprovalResult(
                    anomaly_id=anomaly_id,
                    action_id=action_id,
                    approval=ApprovalStatus.BLOCKED,
                    execution_status=ExecutionStatus.NOT_EXECUTED,
                    message=(
                        "approval refused: spacecraft state changed and the action no longer "
                        "passes safety verification (" + "; ".join(recheck.violations) + ")"
                    ),
                )

        self.memory.set_action_status(
            pending.action_row_id, approval_status=ApprovalStatus.APPROVED.value, operator=operator
        )
        self.memory.audit(
            audit_id=uuid.uuid4().hex[:12],
            cycle_id=f"approval-{anomaly_id}",
            stage="approval",
            actor=operator,
            detail={"anomaly_id": anomaly_id, "action_id": action_id, "approved": True, "note": note},
        )
        self._execute(pending, operator=operator)
        self.bus.publish(
            "approval", {"anomaly_id": anomaly_id, "action_id": action_id, "approved": True}
        )
        return ApprovalResult(
            anomaly_id=anomaly_id,
            action_id=action_id,
            approval=ApprovalStatus.APPROVED,
            execution_status=ExecutionStatus.EXECUTED,
            message=f"'{action_id}' approved by {operator} and queued for execution",
        )

    # ===================================================================== #
    # Introspection
    # ===================================================================== #

    def status(self) -> dict:
        with self._lock:
            return {
                "cycles": self.cycles,
                "anomalies_opened": self.anomalies_opened,
                "suppressed": self.suppressed_count,
                "awaiting_approval": [
                    {
                        "anomaly_id": anomaly_id,
                        "action_id": p.option.action_id,
                        "risk_level": p.option.risk_level.value,
                        "description": p.option.description,
                    }
                    for anomaly_id, p in list(self._awaiting_approval.items())
                ],
                "measuring_outcome": [
                    {
                        "spacecraft_id": craft,
                        "action_id": p.option.action_id,
                        "evaluate_at_seq": p.evaluate_at_seq,
                    }
                    for craft, p in list(self._pending.items())
                ],
                "detector_fitted": self.detector.is_fitted,
                "detector_training_frames": self.detector.n_train,
                "llm": self.llm.status,
                "autonomy_limit": self.s.auto_execute_max_risk.value,
            }

    def pending_approvals(self) -> list[dict]:
        return self.status()["awaiting_approval"]
