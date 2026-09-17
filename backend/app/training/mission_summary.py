"""Mission summaries as training signal.

The per-frame examples in `corpus.py` teach Astrix-LM to answer one question:
given this detection context, what is the fault and what should be done? They
cannot teach it anything that only becomes visible across a whole mission —
that a thermal transient during eclipse was benign every time, that a wheel
anomaly which followed a slew recovered on its own, that two recoveries in a row
on the same subsystem meant the first one did not work.

So at the end of every mission the runner hands its event history here. This
module folds it into one narrative example — phases flown, anomalies opened,
what was diagnosed, what was executed, whether it worked, and what went
unexplained — and writes it to the same encrypted, hash-chained corpus. Those
summaries are what `scripts/train_astrix_lm.py` fine-tunes on, and what the
open-world path in `novelty.py` searches when a live anomaly matches nothing in
the catalogue.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

log = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You are Astrix-LM reviewing a completed mission. Given the mission's phases, "
    "anomalies, recovery decisions and outcomes, return the mission-level findings as "
    "JSON: what recurred, which recoveries were effective, which anomalies went "
    "unexplained, and what to watch for next flight. Your output is advisory and must "
    "pass the deterministic safety engine before anything acts on it."
)

# Events that describe the arc of a mission rather than one frame of it.
NARRATIVE_EVENTS = frozenset(
    {
        "mission_started",
        "launch_milestone",
        "orbit_acquired",
        "fault_injected",
        "fault_cleared",
        "detection",
        "diagnosis",
        "approval",
        "executed",
        "outcome",
        "anomaly_closed",
        "novel_anomaly",
        "mission_stopped",
        "runner_error",
    }
)


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def build_mission_summary(events: list[dict[str, Any]], mission: dict[str, Any]) -> dict[str, Any]:
    """Fold one mission's event history into a structured summary.

    Pure: it reads events and returns a dict. Nothing here touches the corpus, so
    it is testable against a recorded event list and safe to call from the
    runner's shutdown path.
    """
    milestones: list[str] = []
    injected: list[dict[str, Any]] = []
    diagnoses: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    novel: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    severities: Counter[str] = Counter()
    errors: list[str] = []

    for event in events:
        kind = event.get("type")
        if kind not in NARRATIVE_EVENTS:
            continue
        body = _payload(event)
        if kind == "launch_milestone":
            milestones.append(str(body.get("name") or body.get("milestone") or ""))
        elif kind == "orbit_acquired":
            milestones.append("orbit_acquired")
        elif kind == "fault_injected":
            injected.append(
                {
                    "scenario": body.get("scenario") or body.get("key"),
                    "title": body.get("title"),
                    "is_fault": body.get("is_fault", True),
                    "seq": body.get("seq"),
                }
            )
        elif kind == "detection":
            severity = body.get("severity")
            if severity:
                severities[str(severity)] += 1
        elif kind == "diagnosis":
            diagnoses.append(
                {
                    "subsystem": body.get("subsystem"),
                    "failure_mode": body.get("failure_mode") or "unclassified",
                    "component": body.get("component"),
                    "confidence": body.get("confidence"),
                    "reasoner": body.get("reasoner"),
                }
            )
        elif kind == "approval":
            approvals.append({"action_id": body.get("action_id"), "approved": body.get("approved")})
        elif kind == "executed":
            executions.append({"action_id": body.get("action_id"), "seq": body.get("seq")})
        elif kind == "outcome":
            outcomes.append(
                {
                    "action_id": body.get("action_id"),
                    "outcome": body.get("outcome"),
                    "effectiveness": body.get("effectiveness"),
                    "side_effects": body.get("side_effects") or [],
                }
            )
        elif kind == "novel_anomaly":
            novel.append(
                {
                    "signature": body.get("signature"),
                    "channels": body.get("deviating_parameters") or [],
                    "severity": body.get("severity"),
                    "proposed_action": body.get("proposed_action"),
                }
            )
        elif kind == "runner_error":
            errors.append(str(body.get("message") or "")[:200])

    findings = _findings(injected, diagnoses, executions, outcomes, novel)

    return {
        "mission": {
            "spacecraft_id": mission.get("spacecraft_id"),
            "scenario": mission.get("active_scenario_title"),
            "vehicle": (mission.get("vehicle") or {}).get("name"),
            "phase_reached": mission.get("phase"),
            "simulated_seconds": mission.get("simulated_seconds"),
            "safe_mode": mission.get("safe_mode"),
            "milestones": [m for m in milestones if m],
        },
        "observed": {
            "faults_injected": injected,
            "severity_counts": dict(severities),
            "diagnoses": diagnoses,
            "approvals": approvals,
            "executions": executions,
            "outcomes": outcomes,
            "novel_anomalies": novel,
            "runner_errors": errors,
        },
        "findings": findings,
    }


def _findings(
    injected: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    novel: list[dict[str, Any]],
) -> dict[str, Any]:
    """The part a model can actually learn from: what repeated, and what worked."""
    modes = Counter(d["failure_mode"] for d in diagnoses if d.get("failure_mode"))
    recurring = [mode for mode, count in modes.items() if count > 1 and mode != "unclassified"]

    effective: dict[str, list[float]] = {}
    for record in outcomes:
        action = record.get("action_id")
        if not action:
            continue
        effective.setdefault(action, []).append(float(record.get("effectiveness") or 0.0))
    action_scores = {
        action: round(sum(scores) / len(scores), 3) for action, scores in effective.items() if scores
    }

    repeated_on_same = [
        action for action, count in Counter(e.get("action_id") for e in executions).items()
        if action and count > 1
    ]

    unexplained = modes.get("unclassified", 0) + len(novel)
    injected_keys = {f.get("scenario") for f in injected if f.get("is_fault")}
    diagnosed_modes = {d.get("failure_mode") for d in diagnoses}

    return {
        "recurring_failure_modes": recurring,
        "action_effectiveness": action_scores,
        "actions_repeated": repeated_on_same,
        "unexplained_anomalies": unexplained,
        # An injected fault with no matching diagnosis is the most useful training
        # signal in the whole summary: it is a gap in the catalogue, not noise.
        "undiagnosed_injections": sorted(k for k in injected_keys if k and k not in diagnosed_modes),
        "novel_signatures": [n.get("signature") for n in novel if n.get("signature")],
        "verdict": _verdict(outcomes, unexplained, executions),
    }


def _verdict(outcomes: list[dict[str, Any]], unexplained: int, executions: list[dict[str, Any]]) -> str:
    if not executions and not unexplained:
        return "NOMINAL"
    failed = sum(1 for o in outcomes if o.get("outcome") == "FAILED")
    successful = sum(1 for o in outcomes if o.get("outcome") == "SUCCESSFUL")
    if failed and failed >= successful:
        return "RECOVERY_INEFFECTIVE"
    if unexplained:
        return "UNEXPLAINED_BEHAVIOUR"
    if successful:
        return "RECOVERED"
    return "INCONCLUSIVE"


def summary_to_example(summary: dict[str, Any]) -> dict[str, Any] | None:
    """Corpus example from a mission summary, or None if nothing happened.

    Shaped like the per-frame examples so both kinds train the same heads, with
    `scope: mission` distinguishing them for filtering and for the export.
    """
    observed = summary.get("observed", {})
    findings = summary.get("findings", {})
    if not observed.get("diagnoses") and not observed.get("novel_anomalies"):
        return None  # a quiet mission teaches nothing worth a row

    mission = summary.get("mission", {})
    return {
        "scope": "mission",
        "situation": {
            "scenario": mission.get("scenario"),
            "phase_reached": mission.get("phase_reached"),
            "simulated_seconds": mission.get("simulated_seconds"),
            "severity_counts": observed.get("severity_counts", {}),
            "faults_injected": [f.get("scenario") for f in observed.get("faults_injected", [])],
            "milestones": mission.get("milestones", []),
        },
        "decision": {
            "diagnoses": observed.get("diagnoses", []),
            "executions": observed.get("executions", []),
            "outcomes": observed.get("outcomes", []),
            "findings": findings,
        },
        "labels": {
            "subsystem": _dominant(observed.get("diagnoses", []), "subsystem") or "MISSION",
            "failure_mode": _dominant(observed.get("diagnoses", []), "failure_mode") or "none",
            "action_id": (observed.get("executions") or [{}])[0].get("action_id") or "none",
            "reasoner": _dominant(observed.get("diagnoses", []), "reasoner") or "DETERMINISTIC",
            "safety_status": findings.get("verdict", "INCONCLUSIVE"),
        },
        "spacecraft_id": mission.get("spacecraft_id"),
    }


def _dominant(records: list[dict[str, Any]], field: str) -> str | None:
    values = Counter(str(r.get(field)) for r in records if r.get(field))
    return values.most_common(1)[0][0] if values else None


def render_narrative(summary: dict[str, Any]) -> str:
    """Plain-language mission debrief, for the assistant and the export's prompt side."""
    mission = summary.get("mission", {})
    observed = summary.get("observed", {})
    findings = summary.get("findings", {})

    lines = [
        f"Mission on {mission.get('spacecraft_id') or 'ASTRIX-01'} "
        f"({mission.get('scenario') or 'nominal profile'}) reached {mission.get('phase_reached') or 'IDLE'} "
        f"after {round(float(mission.get('simulated_seconds') or 0))} simulated seconds."
    ]
    faults = observed.get("faults_injected", [])
    if faults:
        lines.append(
            "Faults injected: " + ", ".join(str(f.get("title") or f.get("scenario")) for f in faults) + "."
        )
    diagnoses = observed.get("diagnoses", [])
    if diagnoses:
        modes = Counter(d.get("failure_mode") for d in diagnoses)
        lines.append(
            "Diagnosed: "
            + ", ".join(f"{mode} ×{count}" for mode, count in modes.most_common())
            + "."
        )
    outcomes = observed.get("outcomes", [])
    if outcomes:
        lines.append(
            "Recovery outcomes: "
            + ", ".join(f"{o.get('action_id')} → {o.get('outcome')}" for o in outcomes)
            + "."
        )
    if findings.get("recurring_failure_modes"):
        lines.append("Recurring: " + ", ".join(findings["recurring_failure_modes"]) + ".")
    if findings.get("undiagnosed_injections"):
        lines.append(
            "Injected but never diagnosed: " + ", ".join(findings["undiagnosed_injections"]) + " — "
            "a gap in the failure-mode catalogue."
        )
    if observed.get("novel_anomalies"):
        lines.append(
            f"{len(observed['novel_anomalies'])} anomaly signature(s) matched nothing in the "
            "catalogue and were captured for open-world review."
        )
    lines.append(f"Verdict: {findings.get('verdict', 'INCONCLUSIVE')}.")
    return " ".join(lines)
