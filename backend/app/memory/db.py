"""Structured mission memory — SQLAlchemy models (master context §28).

Row classes carry a `Row` suffix so they never collide with the Pydantic models
of the same concept in `core/schemas.py`. The rule of thumb: Pydantic crosses
process boundaries, `*Row` persists.

Defaults to SQLite so the demo runs with zero infrastructure; point
`ASTRIX_DATABASE_URL` at Postgres and the same models apply unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SpacecraftRow(Base):
    __tablename__ = "spacecraft"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="GREEN")
    launch_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    operational_mode: Mapped[str] = mapped_column(String(32), default="NOMINAL")
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, default=list)


class MissionRow(Base):
    __tablename__ = "mission"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    spacecraft_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    objectives: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    summary: Mapped[str] = mapped_column(Text, default="")


class SubsystemRow(Base):
    __tablename__ = "subsystem"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spacecraft_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="NOMINAL")
    capability: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[str] = mapped_column(Text, default="")


class TelemetryRow(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spacecraft_id: Mapped[str] = mapped_column(String(64), index=True)
    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(32), default="NOMINAL")
    # Full frame retained as JSON: the anomaly record needs the exact telemetry
    # the decision was made on, and a partial copy would break the audit trail.
    frame: Mapped[dict] = mapped_column(JSON, default=dict)


class AnomalyRow(Base):
    __tablename__ = "anomaly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spacecraft_id: Mapped[str] = mapped_column(String(64), index=True)
    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    subsystem: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    anomaly_type: Mapped[str] = mapped_column(String(96), default="unclassified")
    ml_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str] = mapped_column(String(16), default="NORMAL", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    frame: Mapped[dict] = mapped_column(JSON, default=dict)
    detection: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    diagnoses: Mapped[list["DiagnosisRow"]] = relationship(back_populates="anomaly")
    actions: Mapped[list["RecoveryActionRow"]] = relationship(back_populates="anomaly")


class DiagnosisRow(Base):
    __tablename__ = "diagnosis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anomaly_id: Mapped[int] = mapped_column(ForeignKey("anomaly.id"), index=True)
    subsystem: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    component: Mapped[str | None] = mapped_column(String(96), nullable=True)
    probable_cause: Mapped[str] = mapped_column(Text)
    failure_mode: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    reasoner: Mapped[str] = mapped_column(String(16), default="DETERMINISTIC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    anomaly: Mapped[AnomalyRow] = relationship(back_populates="diagnoses")


class RecoveryActionRow(Base):
    __tablename__ = "recovery_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anomaly_id: Mapped[int] = mapped_column(ForeignKey("anomaly.id"), index=True)
    action_id: Mapped[str] = mapped_column(String(96), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(16), default="GREEN")
    simulation_status: Mapped[str] = mapped_column(String(16), default="NOT_RUN")
    approval_status: Mapped[str] = mapped_column(String(24), default="PENDING_APPROVAL")
    execution_status: Mapped[str] = mapped_column(String(24), default="NOT_EXECUTED")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    safety: Mapped[dict] = mapped_column(JSON, default=dict)
    simulation: Mapped[dict] = mapped_column(JSON, default=dict)
    operator: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    anomaly: Mapped[AnomalyRow] = relationship(back_populates="actions")
    outcome: Mapped["RecoveryOutcomeRow | None"] = relationship(
        back_populates="action", uselist=False
    )


class RecoveryOutcomeRow(Base):
    __tablename__ = "recovery_outcome"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recovery_action_id: Mapped[int] = mapped_column(ForeignKey("recovery_action.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(16), default="UNKNOWN", index=True)
    effectiveness: Mapped[float] = mapped_column(Float, default=0.0)
    side_effects: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    action: Mapped[RecoveryActionRow] = relationship(back_populates="outcome")


class LessonRow(Base):
    __tablename__ = "lesson"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spacecraft_id: Mapped[str] = mapped_column(String(64), index=True)
    mission_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(64), default="general")
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    validation_status: Mapped[str] = mapped_column(String(24), default="UNVALIDATED")
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditRow(Base):
    """Append-only record of every verification decision (§9).

    Nothing in ASTRIX updates or deletes these rows.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64), default="astrix")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


def build_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        # The pipeline touches the session from the request thread and from the
        # background telemetry consumer.
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    return engine


def build_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(database_url: str):
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    return engine
