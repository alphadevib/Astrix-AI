"""Mission Memory Agent — the persistent memory behind ASTRIX (master context §8.4, §9).

This is the project's central differentiator, so it is worth being precise about
what it does. It combines two stores that answer different questions:

* **SQL** answers *countable* questions: how often has this subsystem failed,
  what fraction of `isolate_wheel_3` attempts succeeded, which lesson recurs.
* **Vector** answers *descriptive* questions: what past write-up reads like what
  we are seeing now.

`recall()` merges both into one `MemoryRecall` that every downstream agent
receives. Historical success rates come from SQL, not from the LLM — a ranked
recovery option's `historical_success_rate` is a measured number.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..core.enums import Outcome, Severity, Subsystem
from ..core.schemas import (
    Diagnosis,
    DetectionResult,
    Lesson,
    MemoryHit,
    MemoryRecall,
    MissionLearning,
    RecoveryOption,
    RecoveryPlan,
    RiskAssessment,
    SafetyVerdict,
    SimulationResult,
    SpacecraftProfile,
    TelemetryFrame,
)
from .db import (
    AnomalyRow,
    AuditRow,
    DiagnosisRow,
    LessonRow,
    MissionRow,
    RecoveryActionRow,
    RecoveryOutcomeRow,
    SpacecraftRow,
    TelemetryRow,
)
from .vector import VectorDoc, VectorStore

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MissionMemory:
    def __init__(self, session_factory: sessionmaker, vectors: VectorStore) -> None:
        self.session_factory = session_factory
        self.vectors = vectors

    # ------------------------------------------------------------------ write

    def record_frame(self, frame: TelemetryFrame) -> None:
        with self.session_factory() as session:
            session.add(
                TelemetryRow(
                    spacecraft_id=frame.spacecraft_id,
                    mission_id=frame.mission_id,
                    timestamp=frame.timestamp,
                    seq=frame.seq,
                    mode=frame.mode.value,
                    frame=frame.model_dump(mode="json"),
                )
            )
            session.commit()

    def open_anomaly(
        self,
        frame: TelemetryFrame,
        detection: DetectionResult,
        subsystem: Subsystem,
        anomaly_type: str,
    ) -> int:
        with self.session_factory() as session:
            row = AnomalyRow(
                spacecraft_id=frame.spacecraft_id,
                mission_id=frame.mission_id,
                timestamp=frame.timestamp,
                subsystem=subsystem.value,
                anomaly_type=anomaly_type,
                ml_score=detection.ml_score,
                final_score=detection.final_score,
                severity=detection.severity.value,
                suppressed=detection.suppressed,
                status="SUPPRESSED" if detection.suppressed else "OPEN",
                frame=frame.model_dump(mode="json"),
                detection=detection.model_dump(mode="json"),
            )
            session.add(row)
            session.commit()
            return row.id

    def attach_diagnosis(self, anomaly_id: int, diagnosis: Diagnosis) -> None:
        with self.session_factory() as session:
            session.add(
                DiagnosisRow(
                    anomaly_id=anomaly_id,
                    subsystem=diagnosis.subsystem.value,
                    component=diagnosis.component,
                    probable_cause=diagnosis.probable_cause,
                    failure_mode=diagnosis.failure_mode,
                    confidence=diagnosis.confidence,
                    evidence=diagnosis.evidence,
                    reasoner=diagnosis.reasoner.value,
                )
            )
            anomaly = session.get(AnomalyRow, anomaly_id)
            if anomaly is not None:
                anomaly.subsystem = diagnosis.subsystem.value
                anomaly.anomaly_type = diagnosis.failure_mode or anomaly.anomaly_type
                anomaly.confidence = diagnosis.confidence
            session.commit()

    def attach_risk(self, anomaly_id: int, risk: RiskAssessment) -> None:
        with self.session_factory() as session:
            anomaly = session.get(AnomalyRow, anomaly_id)
            if anomaly is not None:
                anomaly.risk = risk.model_dump(mode="json")
                session.commit()

    def record_action(
        self,
        anomaly_id: int,
        option: RecoveryOption,
        plan: RecoveryPlan,
        safety: SafetyVerdict,
        simulation: SimulationResult | None,
    ) -> int:
        with self.session_factory() as session:
            row = RecoveryActionRow(
                anomaly_id=anomaly_id,
                action_id=option.action_id,
                description=option.description,
                risk_level=safety.effective_risk_level.value,
                simulation_status=simulation.status.value if simulation else "NOT_RUN",
                approval_status=safety.approval.value,
                execution_status="NOT_EXECUTED",
                plan=plan.model_dump(mode="json"),
                safety=safety.model_dump(mode="json"),
                simulation=simulation.model_dump(mode="json") if simulation else {},
            )
            session.add(row)
            session.commit()
            return row.id

    def set_action_status(
        self,
        action_row_id: int,
        approval_status: str | None = None,
        execution_status: str | None = None,
        operator: str | None = None,
    ) -> None:
        with self.session_factory() as session:
            row = session.get(RecoveryActionRow, action_row_id)
            if row is None:
                return
            if approval_status:
                row.approval_status = approval_status
            if execution_status:
                row.execution_status = execution_status
            if operator:
                row.operator = operator
            session.commit()

    def record_outcome(
        self,
        action_row_id: int,
        outcome: Outcome,
        effectiveness: float,
        side_effects: list[str] | None = None,
        notes: str = "",
    ) -> None:
        with self.session_factory() as session:
            session.add(
                RecoveryOutcomeRow(
                    recovery_action_id=action_row_id,
                    outcome=outcome.value,
                    effectiveness=effectiveness,
                    side_effects=side_effects or [],
                    notes=notes,
                )
            )
            action = session.get(RecoveryActionRow, action_row_id)
            if action is not None:
                anomaly = session.get(AnomalyRow, action.anomaly_id)
                if anomaly is not None:
                    anomaly.status = "RESOLVED" if outcome is Outcome.SUCCESSFUL else "MONITORING"
            session.commit()

    def audit(self, audit_id: str, cycle_id: str, stage: str, detail: dict, actor: str = "astrix") -> None:
        with self.session_factory() as session:
            session.add(
                AuditRow(
                    audit_id=audit_id, cycle_id=cycle_id, stage=stage, actor=actor, detail=detail
                )
            )
            session.commit()

    # ---------------------------------------------------------------- lessons

    def record_learning(self, learning: MissionLearning, spacecraft_id: str) -> list[int]:
        """Persist lessons, merging repeats.

        A lesson seen in a second mission is not a new lesson — it is the same
        lesson with stronger evidence. Merging is what turns one-off observations
        into the recurring patterns §10 describes.
        """
        ids: list[int] = []
        with self.session_factory() as session:
            for lesson in learning.lessons:
                existing = session.scalars(
                    select(LessonRow).where(
                        LessonRow.spacecraft_id == spacecraft_id,
                        LessonRow.description == lesson.description,
                    )
                ).first()
                if existing is not None:
                    existing.occurrences += 1
                    existing.recurring = existing.occurrences >= 2
                    existing.confidence = min(0.95, existing.confidence + 0.1)
                    existing.evidence = list({*existing.evidence, *lesson.evidence})
                    session.flush()
                    ids.append(existing.id)
                    continue
                row = LessonRow(
                    spacecraft_id=spacecraft_id,
                    mission_id=learning.mission_id,
                    category=lesson.category,
                    description=lesson.description,
                    evidence=lesson.evidence,
                    confidence=lesson.confidence,
                    validation_status=lesson.validation_status,
                    recurring=lesson.recurring,
                )
                session.add(row)
                session.flush()
                ids.append(row.id)
            session.commit()

        # Mirror into vector memory so future *descriptive* queries find it too.
        for lesson_id, lesson in zip(ids, learning.lessons):
            self.vectors.add(
                VectorDoc(
                    doc_id=f"lesson-{lesson_id}",
                    text=f"Lesson learned ({lesson.category}): {lesson.description}. "
                    f"Evidence: {'; '.join(lesson.evidence)}",
                    metadata={
                        "kind": "lesson",
                        "mission_id": learning.mission_id,
                        "spacecraft_id": spacecraft_id,
                        "title": f"Lesson: {lesson.category}",
                        "outcome": learning.recovery_effectiveness.value,
                    },
                )
            )
        self.vectors.persist()
        return ids

    def add_document(self, doc: VectorDoc) -> None:
        self.vectors.add(doc)
        self.vectors.persist()

    # ------------------------------------------------------------------- read

    def recall(
        self,
        query: str,
        subsystem: Subsystem | None = None,
        k: int = 4,
        min_similarity: float = 0.18,
    ) -> MemoryRecall:
        hits: list[MemoryHit] = []
        for hit in self.vectors.search(query, k=k, min_score=min_similarity):
            meta = hit.doc.metadata or {}
            hits.append(
                MemoryHit(
                    mission_id=str(meta.get("mission_id", "unknown")),
                    spacecraft_id=str(meta.get("spacecraft_id", "unknown")),
                    title=str(meta.get("title", hit.doc.doc_id)),
                    similarity=round(hit.score, 4),
                    subsystem=_as_subsystem(meta.get("subsystem")),
                    failure_mode=meta.get("failure_mode"),
                    recovery_action=meta.get("recovery_action"),
                    outcome=_as_outcome(meta.get("outcome")),
                    excerpt=hit.doc.text[:280],
                    source=str(meta.get("kind", "vector")),
                )
            )
        return MemoryRecall(
            hits=hits,
            lessons=self.relevant_lessons(subsystem),
            action_success_rates=self.action_success_rates(subsystem),
            query=query,
        )

    def relevant_lessons(self, subsystem: Subsystem | None, limit: int = 5) -> list[str]:
        with self.session_factory() as session:
            stmt = select(LessonRow).order_by(
                LessonRow.recurring.desc(), LessonRow.confidence.desc()
            )
            rows = list(session.scalars(stmt))
        if subsystem and subsystem is not Subsystem.UNKNOWN:
            needle = subsystem.value.lower()
            scoped = [
                r
                for r in rows
                if needle in r.category.lower() or needle in r.description.lower()
            ]
            rows = scoped or rows
        return [
            f"{r.description}"
            + (f" (observed in {r.occurrences} missions)" if r.occurrences > 1 else "")
            for r in rows[:limit]
        ]

    def action_success_rates(self, subsystem: Subsystem | None = None) -> dict[str, float]:
        """Measured success rate per action id, from recorded outcomes only."""
        with self.session_factory() as session:
            stmt = (
                select(
                    RecoveryActionRow.action_id,
                    func.count(RecoveryOutcomeRow.id),
                    func.sum(RecoveryOutcomeRow.effectiveness),
                )
                .join(RecoveryOutcomeRow, RecoveryOutcomeRow.recovery_action_id == RecoveryActionRow.id)
                .group_by(RecoveryActionRow.action_id)
            )
            if subsystem and subsystem is not Subsystem.UNKNOWN:
                stmt = stmt.join(AnomalyRow, AnomalyRow.id == RecoveryActionRow.anomaly_id).where(
                    AnomalyRow.subsystem == subsystem.value
                )
            rows = session.execute(stmt).all()
        return {
            action_id: round(float(total or 0.0) / count, 3)
            for action_id, count, total in rows
            if count
        }

    def similar_anomalies(self, subsystem: Subsystem, limit: int = 5) -> list[dict]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(AnomalyRow)
                    .where(AnomalyRow.subsystem == subsystem.value, AnomalyRow.suppressed.is_(False))
                    .order_by(AnomalyRow.timestamp.desc())
                    .limit(limit)
                )
            )
            return [
                {
                    "anomaly_id": r.id,
                    "mission_id": r.mission_id,
                    "timestamp": r.timestamp.isoformat(),
                    "anomaly_type": r.anomaly_type,
                    "severity": r.severity,
                    "status": r.status,
                }
                for r in rows
            ]

    def profile(self, spacecraft_id: str) -> SpacecraftProfile:
        """Spacecraft knowledge profile (§11) — assembled, never hand-maintained."""
        with self.session_factory() as session:
            craft = session.get(SpacecraftRow, spacecraft_id)

            counts = dict(
                session.execute(
                    select(AnomalyRow.subsystem, func.count(AnomalyRow.id))
                    .where(
                        AnomalyRow.spacecraft_id == spacecraft_id,
                        AnomalyRow.suppressed.is_(False),
                    )
                    .group_by(AnomalyRow.subsystem)
                ).all()
            )

            lessons = list(
                session.scalars(select(LessonRow).where(LessonRow.spacecraft_id == spacecraft_id))
            )

            successful = list(
                session.scalars(
                    select(RecoveryActionRow.description)
                    .join(
                        RecoveryOutcomeRow,
                        RecoveryOutcomeRow.recovery_action_id == RecoveryActionRow.id,
                    )
                    .where(RecoveryOutcomeRow.outcome == Outcome.SUCCESSFUL.value)
                    .distinct()
                )
            )

            missions = session.scalar(
                select(func.count(MissionRow.id)).where(MissionRow.spacecraft_id == spacecraft_id)
            )

        return SpacecraftProfile(
            spacecraft_id=spacecraft_id,
            status=(craft.status if craft else "UNKNOWN"),
            capabilities=(craft.capabilities if craft else {}) or {},
            known_limitations=list(
                dict.fromkeys(
                    ((craft.limitations if craft else []) or [])
                    + [l.description for l in lessons if l.category == "limitation"]
                )
            ),
            anomaly_counts={k: int(v) for k, v in counts.items()},
            latent_risks=[l.description for l in lessons if l.recurring],
            successful_recoveries=successful[:8],
            missions_flown=int(missions or 0),
        )

    def open_anomalies(self, spacecraft_id: str | None = None, limit: int = 25) -> list[dict]:
        with self.session_factory() as session:
            stmt = select(AnomalyRow).order_by(AnomalyRow.timestamp.desc()).limit(limit)
            if spacecraft_id:
                stmt = stmt.where(AnomalyRow.spacecraft_id == spacecraft_id)
            rows = list(session.scalars(stmt))
            return [
                {
                    "anomaly_id": r.id,
                    "spacecraft_id": r.spacecraft_id,
                    "mission_id": r.mission_id,
                    "timestamp": r.timestamp.isoformat(),
                    "subsystem": r.subsystem,
                    "anomaly_type": r.anomaly_type,
                    "severity": r.severity,
                    "final_score": r.final_score,
                    "status": r.status,
                    "suppressed": r.suppressed,
                }
                for r in rows
            ]

    def action_for_anomaly(self, anomaly_id: int, action_id: str) -> int | None:
        with self.session_factory() as session:
            row = session.scalars(
                select(RecoveryActionRow)
                .where(
                    RecoveryActionRow.anomaly_id == anomaly_id,
                    RecoveryActionRow.action_id == action_id,
                )
                .order_by(RecoveryActionRow.id.desc())
            ).first()
            return row.id if row else None

    def audit_trail(self, cycle_id: str | None = None, limit: int = 100) -> list[dict]:
        with self.session_factory() as session:
            stmt = select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)
            if cycle_id:
                stmt = stmt.where(AuditRow.cycle_id == cycle_id)
            return [
                {
                    "audit_id": r.audit_id,
                    "cycle_id": r.cycle_id,
                    "stage": r.stage,
                    "actor": r.actor,
                    "detail": r.detail,
                    "created_at": r.created_at.isoformat(),
                }
                for r in session.scalars(stmt)
            ]

    def stats(self) -> dict:
        with self.session_factory() as session:
            return {
                "telemetry_frames": session.scalar(select(func.count(TelemetryRow.id))) or 0,
                "anomalies": session.scalar(select(func.count(AnomalyRow.id))) or 0,
                "diagnoses": session.scalar(select(func.count(DiagnosisRow.id))) or 0,
                "recovery_actions": session.scalar(select(func.count(RecoveryActionRow.id))) or 0,
                "recorded_outcomes": session.scalar(select(func.count(RecoveryOutcomeRow.id))) or 0,
                "lessons": session.scalar(select(func.count(LessonRow.id))) or 0,
                "missions": session.scalar(select(func.count(MissionRow.id))) or 0,
                "vector_documents": self.vectors.count(),
            }


def _as_subsystem(value) -> Subsystem:
    try:
        return Subsystem(str(value).upper())
    except (ValueError, AttributeError):
        return Subsystem.UNKNOWN


def _as_outcome(value) -> Outcome:
    try:
        return Outcome(str(value).upper())
    except (ValueError, AttributeError):
        return Outcome.UNKNOWN


def severity_from_string(value: str) -> Severity:
    try:
        return Severity(value.upper())
    except ValueError:
        return Severity.NORMAL
