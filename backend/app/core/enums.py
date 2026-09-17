"""Controlled vocabularies shared by every ASTRIX stage.

These are the wire contract: n8n nodes, the React dashboard and the safety
rules all switch on these exact strings, so they are `str` enums.
"""

from enum import Enum


class Severity(str, Enum):
    """Four-level anomaly classification (master context §4.1).

    Deliberately *not* a binary NORMAL/ANOMALY flag — the extra levels are what
    let ASTRIX absorb benign deviations without paging an operator.
    """

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.NORMAL: 0,
    Severity.WATCH: 1,
    Severity.WARNING: 2,
    Severity.CRITICAL: 3,
}


class RiskLevel(str, Enum):
    """Action risk tier that decides who is allowed to execute it (§6)."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]


_RISK_RANK = {RiskLevel.GREEN: 0, RiskLevel.YELLOW: 1, RiskLevel.RED: 2}


class OperatingMode(str, Enum):
    """Spacecraft operating mode — the primary context key for false-alarm suppression."""

    NOMINAL = "NOMINAL"
    PAYLOAD_ACTIVE = "PAYLOAD_ACTIVE"
    ECLIPSE = "ECLIPSE"
    COMMS_PASS = "COMMS_PASS"
    MANEUVER = "MANEUVER"
    SAFE_MODE = "SAFE_MODE"


class Subsystem(str, Enum):
    POWER = "POWER"
    THERMAL = "THERMAL"
    ADCS = "ADCS"
    PROPULSION = "PROPULSION"
    COMMS = "COMMS"
    CDH = "CDH"
    PAYLOAD = "PAYLOAD"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ApprovalStatus(str, Enum):
    AUTO_APPROVED = "AUTO_APPROVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class ExecutionStatus(str, Enum):
    NOT_EXECUTED = "NOT_EXECUTED"
    SIMULATED = "SIMULATED"
    EXECUTED = "EXECUTED"


class Outcome(str, Enum):
    SUCCESSFUL = "SUCCESSFUL"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ReasonerKind(str, Enum):
    """Which reasoner produced an agent output.

    Surfaced in the API so the dashboard and the jury can always see whether a
    given decision came from the LLM or the deterministic fallback.
    """

    LLM = "LLM"
    DETERMINISTIC = "DETERMINISTIC"
