"""Seed historical mission memory.

Without this, ASTRIX's first mission has nothing to remember and the Mission
Memory stage is an empty box. The seed supplies three kinds of prior knowledge:

1. **Closed historical anomalies with recorded outcomes** — these are what make
   `historical_success_rate` on a recovery option a measured number rather than
   a guess.
2. **Lessons learned**, including one recurring cross-mission pattern, so §10's
   latent-risk mechanism has something to surface.
3. **Reference case studies** drawn from publicly documented spacecraft events.

A note on the case studies, because it matters for credibility: they are
included as *motivation and test material*, describing what was publicly
reported and which ASTRIX capability the situation exercises. They do not claim
ASTRIX would have prevented any of them (master context §20, §21).

Seeding is idempotent — running it twice does not duplicate anything.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..core.enums import Outcome
from .db import (
    AnomalyRow,
    LessonRow,
    MissionRow,
    RecoveryActionRow,
    RecoveryOutcomeRow,
    SpacecraftRow,
    SubsystemRow,
)
from .vector import VectorDoc, VectorStore

log = logging.getLogger(__name__)

SPACECRAFT_ID = "ASTRIX-01"
_T0 = datetime(2025, 3, 1, tzinfo=timezone.utc)


def _ts(days: float) -> datetime:
    return _T0 + timedelta(days=days)


# --------------------------------------------------------------------------- #
# Historical anomalies: (mission, day, subsystem, type, severity, actions)
# Each action is (action_id, description, risk, outcome, effectiveness, notes)
# --------------------------------------------------------------------------- #

_HISTORY: list[dict] = [
    {
        "mission_id": "XYZ-01",
        "day": 42,
        "subsystem": "ADCS",
        "anomaly_type": "reaction_wheel_degradation",
        "severity": "CRITICAL",
        "score": 0.88,
        "cause": "Reaction wheel #3 bearing degradation: vibration and motor current rose together over ~18 minutes before pointing error became visible.",
        "actions": [
            (
                "restart_wheel_3",
                "Power-cycle reaction wheel #3",
                "YELLOW",
                Outcome.FAILED,
                0.15,
                "Vibration returned to pre-restart levels within 4 minutes — mechanical, not a control-loop latch.",
            ),
            (
                "isolate_wheel_3",
                "Isolate reaction wheel #3 and spin down",
                "YELLOW",
                Outcome.SUCCESSFUL,
                0.93,
                "Vibration collapsed immediately; three-wheel control held pointing within budget.",
            ),
            (
                "switch_redundant_wheel_config",
                "Switch to redundant three-wheel attitude control configuration",
                "YELLOW",
                Outcome.SUCCESSFUL,
                0.91,
                "Momentum redistributed across remaining wheels; payload operations resumed same orbit.",
            ),
        ],
    },
    {
        "mission_id": "XYZ-01",
        "day": 61,
        "subsystem": "ADCS",
        "anomaly_type": "reaction_wheel_degradation",
        "severity": "WARNING",
        "score": 0.64,
        "cause": "Early-stage vibration growth on reaction wheel #2 detected before current rise.",
        "actions": [
            (
                "reduce_wheel_speed",
                "Reduce reaction wheel speeds to lower bearing load",
                "GREEN",
                Outcome.PARTIAL,
                0.58,
                "Slowed the degradation rate but did not arrest it; bought roughly three weeks.",
            ),
        ],
    },
    {
        "mission_id": "XYZ-01",
        "day": 88,
        "subsystem": "POWER",
        "anomaly_type": "battery_capacity_degradation",
        "severity": "WARNING",
        "score": 0.61,
        "cause": "Usable battery capacity fell measurably across repeated deep eclipse discharges.",
        "actions": [
            (
                "enter_power_save",
                "Engage power-saving mode and shed non-essential loads",
                "GREEN",
                Outcome.SUCCESSFUL,
                0.82,
                "Eclipse-exit state of charge recovered above the 45% floor.",
            ),
            (
                "reduce_payload_duty_cycle",
                "Reduce payload duty cycle during eclipse-adjacent passes",
                "GREEN",
                Outcome.SUCCESSFUL,
                0.77,
                "Degradation rate slowed; science return reduced ~12%.",
            ),
        ],
    },
    {
        "mission_id": "ASTRIX-M00",
        "day": 130,
        "subsystem": "THERMAL",
        "anomaly_type": "thermal_load_correlation",
        "severity": "WARNING",
        "score": 0.58,
        "cause": "Bus temperature rose in correlation with sustained high CPU load while battery state of charge was already low.",
        "actions": [
            (
                "reduce_payload_duty_cycle",
                "Throttle onboard processing to reduce thermal load",
                "GREEN",
                Outcome.SUCCESSFUL,
                0.74,
                "Temperature returned inside the envelope within 9 minutes.",
            ),
        ],
    },
    {
        "mission_id": "ASTRIX-M00",
        "day": 141,
        "subsystem": "THERMAL",
        "anomaly_type": "thermal_sensor_fault",
        "severity": "WATCH",
        "score": 0.44,
        "cause": "Primary thermal sensor read 20 °C above the redundant sensor with no corroborating power or CPU signature — instrumentation fault.",
        "actions": [
            (
                "switch_to_redundant_sensor",
                "Select the redundant thermal sensor as primary",
                "GREEN",
                Outcome.SUCCESSFUL,
                0.90,
                "Readings became consistent; no thermal excursion had in fact occurred.",
            ),
        ],
    },
    {
        "mission_id": "ASTRIX-M00",
        "day": 158,
        "subsystem": "COMMS",
        "anomaly_type": "link_degradation_low_elevation",
        "severity": "WATCH",
        "score": 0.41,
        "cause": "Packet loss and latency rose at low ground-station elevation angles — geometry-driven, recurring every comparable pass.",
        "actions": [
            (
                "reduce_downlink_rate",
                "Reduce downlink data rate for the remainder of the pass",
                "GREEN",
                Outcome.SUCCESSFUL,
                0.79,
                "Link closed reliably at the lower rate; no data lost.",
            ),
        ],
    },
    {
        "mission_id": "ASTRIX-M00",
        "day": 175,
        "subsystem": "ADCS",
        "anomaly_type": "gyro_bias_drift",
        "severity": "WARNING",
        "score": 0.6,
        "cause": "Gyro bias drifted slowly, degrading the pointing solution while wheel telemetry stayed nominal.",
        "actions": [
            (
                "recalibrate_gyro_bias",
                "Recalibrate gyro bias against the star tracker",
                "GREEN",
                Outcome.SUCCESSFUL,
                0.86,
                "Pointing solution re-converged; drift recurred at a slower rate after 6 weeks.",
            ),
        ],
    },
]


_LESSONS: list[dict] = [
    {
        "category": "precursor_pattern",
        "description": (
            "Rising reaction-wheel vibration together with rising motor current preceded "
            "wheel degradation by approximately 18 minutes. Treat the pair as a precursor "
            "and begin planning isolation before pointing error becomes visible."
        ),
        "evidence": [
            "XYZ-01 day 42: vibration and current rose together ~18 min before attitude error",
            "XYZ-01 day 61: same pairing observed at an earlier stage",
        ],
        "confidence": 0.82,
        "recurring": True,
        "occurrences": 2,
        "validation_status": "OBSERVED_MULTIPLE_MISSIONS",
    },
    {
        "category": "recovery_effectiveness",
        "description": (
            "Power-cycling a mechanically degraded reaction wheel does not help. Isolation plus "
            "a redundant three-wheel configuration is the effective recovery."
        ),
        "evidence": ["XYZ-01 day 42: restart failed (0.15), isolation succeeded (0.93)"],
        "confidence": 0.88,
        "recurring": False,
        "occurrences": 1,
        "validation_status": "VALIDATED_BY_OUTCOME",
    },
    {
        "category": "latent_risk",
        "description": (
            "Potential latent risk: sustained high computational workload may be associated with "
            "increased thermal stress when battery state of charge is already low. Observed as a "
            "weak correlation across three missions; causality is NOT established."
        ),
        "evidence": [
            "Mission 1: minor temperature spike",
            "Mission 2: temperature spike under high CPU load",
            "ASTRIX-M00 day 130: temperature spike with high CPU load and declining state of charge",
        ],
        "confidence": 0.46,
        "recurring": True,
        "occurrences": 3,
        "validation_status": "WEAKLY_CORRELATED_UNVALIDATED",
    },
    {
        "category": "limitation",
        "description": "Battery usable capacity degrades measurably under repeated deep eclipse discharge.",
        "evidence": ["XYZ-01 day 88 capacity trend"],
        "confidence": 0.8,
        "recurring": False,
        "occurrences": 1,
        "validation_status": "VALIDATED_BY_OUTCOME",
    },
    {
        "category": "limitation",
        "description": "Reaction wheel #3 shows elevated vibration at sustained RPM above 2,800.",
        "evidence": ["XYZ-01 day 42", "XYZ-01 day 61"],
        "confidence": 0.75,
        "recurring": False,
        "occurrences": 1,
        "validation_status": "OBSERVED",
    },
    {
        "category": "false_alarm_reduction",
        "description": (
            "A primary/redundant thermal sensor disagreement above ~10 °C with no power or CPU "
            "corroboration indicates an instrumentation fault, not a thermal excursion."
        ),
        "evidence": ["ASTRIX-M00 day 141"],
        "confidence": 0.84,
        "recurring": False,
        "occurrences": 1,
        "validation_status": "VALIDATED_BY_OUTCOME",
    },
    {
        "category": "operational_constraint",
        "description": "Avoid high-RPM pointing campaigns during low-battery conditions.",
        "evidence": ["XYZ-01 day 88", "ASTRIX-M00 day 130"],
        "confidence": 0.7,
        "recurring": False,
        "occurrences": 1,
        "validation_status": "OPERATIONAL_RULE",
    },
]


# --------------------------------------------------------------------------- #
# Reference case studies (publicly documented events)
# --------------------------------------------------------------------------- #

_CASE_STUDIES: list[dict] = [
    {
        "doc_id": "case-swift-reaction-wheel",
        "title": "Case study: Swift reaction wheel failure",
        "subsystem": "ADCS",
        "failure_mode": "reaction_wheel_failure",
        "recovery_action": "isolate_wheel_and_reconfigure",
        "outcome": "SUCCESSFUL",
        "text": (
            "Publicly reported event. A reaction wheel on the Swift observatory failed "
            "mechanically. The spacecraft entered a protective safe mode and the operations team "
            "reconfigured attitude control to work with the remaining wheels, allowing the mission "
            "to continue. Capability exercised by this class of event: detect wheel degradation from "
            "vibration and motor current, isolate the suspect wheel, evaluate remaining redundancy, "
            "simulate degraded attitude control, and verify that pointing requirements are still "
            "achievable before committing. Included as motivation and as test material; no claim is "
            "made that an autonomous system would have changed this outcome."
        ),
    },
    {
        "doc_id": "case-kepler-wheel-failures",
        "title": "Case study: Kepler wheel failures and mission re-planning",
        "subsystem": "ADCS",
        "failure_mode": "multiple_reaction_wheel_failure",
        "recovery_action": "replan_mission_objectives",
        "outcome": "PARTIAL",
        "text": (
            "Publicly reported event. Two reaction wheels on Kepler failed, preventing the precise "
            "pointing its original observing strategy required. Engineers developed the K2 mission "
            "concept, using the remaining wheels together with thrusters and solar radiation "
            "pressure for stability. Capability exercised: assess degraded resources, determine "
            "whether mission objectives remain achievable, and generate an alternative operating "
            "mode rather than declaring the mission lost. This is the strongest argument for "
            "persistent mission memory: the recovery was a re-plan, not a repair."
        ),
    },
    {
        "doc_id": "case-hubble-gyroscope",
        "title": "Case study: Hubble gyroscope issues and reduced-gyro operation",
        "subsystem": "ADCS",
        "failure_mode": "gyro_degradation",
        "recovery_action": "degraded_mode_operation",
        "outcome": "SUCCESSFUL",
        "text": (
            "Publicly reported event. Gyroscope problems on the Hubble Space Telescope led to "
            "safe-mode entries and, over time, to operating in reduced-gyro configurations. "
            "Capability exercised: detect a sensor outlier, cross-compare redundant sensors to "
            "isolate the suspect unit, evaluate a degraded configuration, and verify pointing "
            "stability under it before adopting it operationally."
        ),
    },
    {
        "doc_id": "case-maven-cascade",
        "title": "Case study: MAVEN cascading attitude-to-power failure",
        "subsystem": "ADCS",
        "failure_mode": "attitude_power_cascade",
        "recovery_action": "attitude_and_power_recovery",
        "outcome": "FAILED",
        "text": (
            "Publicly reported event. After emerging from behind Mars, MAVEN developed an "
            "unexpected rapid rotation. The resulting power drain and loss of communications "
            "capability contributed to the loss of recovery ability. Capability exercised: "
            "recognise a cascade rather than isolated anomalies — attitude anomaly leads to excess "
            "power consumption, which drives battery decline, which threatens the communications "
            "link and therefore recovery itself. Cascade detection is why risk assessment in ASTRIX "
            "considers cross-subsystem consequences and time-to-impact, not just severity."
        ),
    },
    {
        "doc_id": "case-spirit-power-mobility",
        "title": "Case study: Spirit rover power and mobility degradation",
        "subsystem": "POWER",
        "failure_mode": "power_and_mobility_degradation",
        "recovery_action": "energy_conservation_and_replan",
        "outcome": "PARTIAL",
        "text": (
            "Publicly reported event. Dust accumulation and storms reduced available solar power for "
            "the Spirit rover; the vehicle later became embedded and suffered a wheel failure, and "
            "operations were eventually adapted toward stationary science. Capability exercised: "
            "power prediction, energy conservation planning, mobility fault diagnosis, and "
            "repurposing mission objectives when a capability is permanently lost."
        ),
    },
    {
        "doc_id": "case-opportunity-flash",
        "title": "Case study: Opportunity flash-memory adaptation",
        "subsystem": "CDH",
        "failure_mode": "storage_degradation",
        "recovery_action": "switch_operating_configuration",
        "outcome": "SUCCESSFUL",
        "text": (
            "Publicly reported event. Flash-memory problems on the Opportunity rover required "
            "operational adaptation, including operating without flash storage. Capability "
            "exercised: detect storage write errors, isolate the failing medium, switch operating "
            "configuration, and prioritise preservation of critical telemetry under reduced storage."
        ),
    },
    {
        "doc_id": "case-pathfinder-resets",
        "title": "Case study: Mars Pathfinder recurring resets",
        "subsystem": "CDH",
        "failure_mode": "task_scheduling_fault",
        "recovery_action": "software_reconfiguration",
        "outcome": "SUCCESSFUL",
        "text": (
            "Publicly reported event. Mars Pathfinder experienced recurring computer resets traced "
            "to a priority-inversion problem in task scheduling, and was recovered by remote "
            "software reconfiguration. Capability exercised: monitor task and resource behaviour "
            "rather than only physical sensors, recognise an abnormal recurring pattern, reason "
            "about a likely software interaction, and support remote recovery. The durable lesson is "
            "that the ability to diagnose and reconfigure remotely is itself a mission capability."
        ),
    },
    {
        "doc_id": "case-elsa-d-thrusters",
        "title": "Case study: ELSA-d propulsion degradation",
        "subsystem": "PROPULSION",
        "failure_mode": "partial_propulsion_loss",
        "recovery_action": "assess_remaining_capability_and_replan",
        "outcome": "PARTIAL",
        "text": (
            "Publicly reported event. Several of ELSA-d's thrusters experienced technical "
            "difficulties, affecting the planned orbital debris-capture demonstration. Capability "
            "exercised: assess remaining propulsion authority, determine which maneuvers are still "
            "feasible, simulate alternatives, and re-scope mission objectives to what the surviving "
            "capability supports."
        ),
    },
    {
        "doc_id": "case-preflight-verification",
        "title": "Case study: interface and specification failures (Mars Climate Orbiter, Ariane 5 Flight 501)",
        "subsystem": "UNKNOWN",
        "failure_mode": "interface_specification_fault",
        "recovery_action": "preflight_verification",
        "outcome": "FAILED",
        "text": (
            "Publicly reported events. The loss of Mars Climate Orbiter involved a unit-mismatch "
            "interface problem between ground software and spacecraft navigation; Ariane 5 Flight "
            "501 was lost following a software and specification failure in the inertial reference "
            "system. Both are best understood as preflight verification problems rather than "
            "telemetry anomaly detection problems: the relevant checks are unit consistency, "
            "interface schema validation, parameter-range and physical-plausibility checking, and "
            "simulation of off-nominal conditions. An in-flight anomaly detector would not by itself "
            "have prevented either loss, and ASTRIX does not claim otherwise. They motivate the "
            "deterministic verification layer, not the detector."
        ),
    },
]


# --------------------------------------------------------------------------- #
# Recovery procedures (retrieved by the Recovery Agent)
# --------------------------------------------------------------------------- #

_PROCEDURES: list[dict] = [
    {
        "doc_id": "proc-wheel-isolation",
        "title": "Procedure: reaction wheel isolation and redundant reconfiguration",
        "subsystem": "ADCS",
        "failure_mode": "reaction_wheel_degradation",
        "recovery_action": "isolate_wheel_3",
        "outcome": "SUCCESSFUL",
        "text": (
            "Preconditions: at least three healthy reaction wheels must remain after isolation, "
            "battery state of charge at or above 30%, and the momentum redistribution transient must "
            "fit inside the pointing budget. Steps: command the degraded wheel to spin down under "
            "controlled torque; redistribute momentum across the remaining wheels; re-tune the "
            "attitude control gains for the three-wheel configuration; verify pointing error settles "
            "inside budget within 60 seconds. Do not isolate a wheel if fewer than three healthy "
            "wheels would remain — three-axis authority is lost and safe mode is the correct action "
            "instead. Power-cycling a mechanically degraded wheel is not effective."
        ),
    },
    {
        "doc_id": "proc-power-conservation",
        "title": "Procedure: power conservation under degraded battery capacity",
        "subsystem": "POWER",
        "failure_mode": "battery_capacity_degradation",
        "recovery_action": "enter_power_save",
        "outcome": "SUCCESSFUL",
        "text": (
            "Shed non-essential loads in priority order: payload processing, then non-critical "
            "heaters, then downlink data rate. Maintain a state-of-charge floor of 30% through "
            "eclipse; below 15% prohibit all non-essential high-power actions. If eclipse-exit state "
            "of charge trends below the floor across consecutive orbits, reduce payload duty cycle "
            "rather than deepening discharge — discharge depth drives further capacity loss."
        ),
    },
    {
        "doc_id": "proc-thermal-management",
        "title": "Procedure: thermal excursion management",
        "subsystem": "THERMAL",
        "failure_mode": "thermal_load_correlation",
        "recovery_action": "reduce_payload_duty_cycle",
        "outcome": "SUCCESSFUL",
        "text": (
            "First establish whether the excursion is real: compare primary and redundant sensors. "
            "A disagreement above roughly 10 °C with no power or CPU corroboration indicates an "
            "instrumentation fault — select the redundant sensor rather than shedding load. If both "
            "sensors agree, reduce thermal input: throttle onboard processing, reduce payload duty "
            "cycle, and where attitude permits adjust orientation to reduce solar input. Prohibit any "
            "action that increases thermal load while above the critical limit."
        ),
    },
    {
        "doc_id": "proc-gyro-recalibration",
        "title": "Procedure: gyro bias recalibration",
        "subsystem": "ADCS",
        "failure_mode": "gyro_bias_drift",
        "recovery_action": "recalibrate_gyro_bias",
        "outcome": "SUCCESSFUL",
        "text": (
            "Confirm wheel telemetry is nominal before attributing pointing error to the gyro — "
            "wheel degradation and gyro drift both present as growing attitude error but require "
            "opposite recoveries. Cross-check the gyro solution against the star tracker over at "
            "least one quiet arc, estimate the bias, and apply the correction. Expect drift to "
            "recur; schedule periodic recalibration rather than treating it as closed."
        ),
    },
    {
        "doc_id": "proc-link-management",
        "title": "Procedure: degraded communication link management",
        "subsystem": "COMMS",
        "failure_mode": "link_degradation_low_elevation",
        "recovery_action": "reduce_downlink_rate",
        "outcome": "SUCCESSFUL",
        "text": (
            "Distinguish geometry-driven degradation from hardware degradation: low-elevation loss "
            "recurs predictably every comparable pass and recovers as elevation rises, while hardware "
            "degradation persists across geometries. For geometry, reduce the downlink rate and "
            "re-prioritise critical telemetry first. If the link is unavailable entirely, permit only "
            "pre-approved onboard safe actions until contact is re-established."
        ),
    },
]


_MISSION_REPORTS: list[dict] = [
    {
        "doc_id": "report-xyz-01",
        "title": "Mission report: XYZ-01 (Earth observation)",
        "mission_id": "XYZ-01",
        "subsystem": "ADCS",
        "failure_mode": "reaction_wheel_degradation",
        "recovery_action": "isolate_wheel_3",
        "outcome": "SUCCESSFUL",
        "text": (
            "Objectives: Earth observation. Successful capabilities: high-resolution imaging, "
            "low-power communication, autonomous navigation. Known weaknesses: reaction wheel "
            "vibration at high RPM, battery degradation during eclipse, communication degradation at "
            "low elevation. Observed non-critical issues: temperature spikes, delayed telemetry, "
            "sensor drift. Anomalies: reaction wheel #3 degradation (day 42) with rising vibration "
            "and motor current over approximately 18 minutes before pointing error appeared; early "
            "wheel #2 vibration growth (day 61); battery capacity degradation (day 88). Recovery "
            "actions: wheel #3 power cycle failed; wheel isolation plus redundant three-wheel "
            "configuration succeeded; reduced wheel speed slowed but did not arrest wheel #2 "
            "degradation; power-saving mode restored eclipse-exit state of charge. Unresolved risks: "
            "wheel bearing wear remained, battery degradation continued to accelerate. Lesson "
            "learned: avoid high-RPM pointing campaigns during low-battery conditions."
        ),
    },
    {
        "doc_id": "report-astrix-m00",
        "title": "Mission report: ASTRIX-M00 (technology demonstration)",
        "mission_id": "ASTRIX-M00",
        "subsystem": "THERMAL",
        "failure_mode": "thermal_load_correlation",
        "recovery_action": "reduce_payload_duty_cycle",
        "outcome": "SUCCESSFUL",
        "text": (
            "Objectives: autonomy technology demonstration. Anomalies: bus temperature rise "
            "correlated with sustained high CPU load while state of charge was already declining "
            "(day 130); primary thermal sensor fault reading 20 °C above the redundant sensor with no "
            "corroborating power or compute signature (day 141); communication link degradation at "
            "low ground-station elevation, recurring every comparable pass (day 158); gyro bias drift "
            "degrading the pointing solution with nominal wheel telemetry (day 175). Recovery "
            "actions: processing throttled; redundant thermal sensor selected; downlink rate reduced; "
            "gyro bias recalibrated against the star tracker. Recovery effectiveness: all successful. "
            "Unresolved risks: gyro drift recurred at a slower rate after six weeks. Potential latent "
            "pattern: high compute load may be associated with thermal stress under low-power "
            "conditions — weakly correlated across three missions, causality not established."
        ),
    },
]


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def seed_structured(session_factory: sessionmaker) -> bool:
    """Populate SQL mission memory. Returns True if seeding ran."""
    with session_factory() as session:
        if session.get(SpacecraftRow, SPACECRAFT_ID) is not None:
            return False

        session.add(
            SpacecraftRow(
                id=SPACECRAFT_ID,
                name="ASTRIX-01",
                status="GREEN",
                launch_date=_T0,
                operational_mode="NOMINAL",
                capabilities={
                    "Imaging": "Excellent",
                    "Communication": "Good",
                    "Navigation": "Excellent",
                    "Power": "Moderate",
                    "Autonomy": "Good",
                },
                # Design-time limitations known before flight. Operational
                # limitations discovered in flight arrive separately, as lessons
                # with category "limitation" — keeping the two sources disjoint
                # avoids the profile listing near-duplicates of the same fact.
                limitations=[
                    "Three-axis attitude control requires at least 3 of 4 reaction wheels",
                    "Downlink degrades below 15 degrees ground-station elevation",
                    "Limited thermal headroom above 45 C",
                ],
            )
        )

        for name, type_, capability, limitation in [
            ("Power", "POWER", "480 Wh battery, 240 W array", "Capacity fade under deep discharge"),
            ("Thermal", "THERMAL", "Passive radiator plus heaters", "Limited headroom above 45 °C"),
            ("ADCS", "ADCS", "4 reaction wheels, gyro, star tracker", "3-axis control needs 3 wheels"),
            ("Propulsion", "PROPULSION", "Monopropellant, 78% remaining", "Limited delta-v budget"),
            ("Comms", "COMMS", "S-band downlink", "Degrades below 15° elevation"),
            ("CDH", "CDH", "Onboard processor, 2 GB store", "Thermal coupling at high load"),
            ("Payload", "PAYLOAD", "High-resolution imager", "High power and thermal draw"),
        ]:
            session.add(
                SubsystemRow(
                    spacecraft_id=SPACECRAFT_ID,
                    name=name,
                    type=type_,
                    status="NOMINAL",
                    capability=capability,
                    limitations=limitation,
                )
            )

        for mission_id, name, objectives, start, end, status in [
            ("XYZ-01", "XYZ-01 Earth observation", "Earth observation", _ts(0), _ts(120), "COMPLETED"),
            (
                "ASTRIX-M00",
                "ASTRIX-M00 autonomy demonstration",
                "Autonomy technology demonstration",
                _ts(120),
                _ts(200),
                "COMPLETED",
            ),
            (
                "ASTRIX-M01",
                "ASTRIX-M01 operational mission",
                "Earth observation with autonomous fault management",
                _ts(210),
                None,
                "ACTIVE",
            ),
        ]:
            session.add(
                MissionRow(
                    id=mission_id,
                    spacecraft_id=SPACECRAFT_ID,
                    name=name,
                    objectives=objectives,
                    start_date=start,
                    end_date=end,
                    status=status,
                )
            )

        for event in _HISTORY:
            anomaly = AnomalyRow(
                spacecraft_id=SPACECRAFT_ID,
                mission_id=event["mission_id"],
                timestamp=_ts(event["day"]),
                subsystem=event["subsystem"],
                anomaly_type=event["anomaly_type"],
                ml_score=event["score"],
                final_score=event["score"],
                severity=event["severity"],
                confidence=0.85,
                status="RESOLVED",
                frame={},
                detection={"historical": True},
            )
            session.add(anomaly)
            session.flush()

            for action_id, description, risk, outcome, effectiveness, notes in event["actions"]:
                action = RecoveryActionRow(
                    anomaly_id=anomaly.id,
                    action_id=action_id,
                    description=description,
                    risk_level=risk,
                    simulation_status="PASS",
                    approval_status="APPROVED",
                    execution_status="EXECUTED",
                    plan={"historical": True, "probable_cause": event["cause"]},
                    safety={"status": "PASS"},
                    simulation={"status": "PASS"},
                    operator="historical-record",
                )
                session.add(action)
                session.flush()
                session.add(
                    RecoveryOutcomeRow(
                        recovery_action_id=action.id,
                        outcome=outcome.value,
                        effectiveness=effectiveness,
                        side_effects=[],
                        notes=notes,
                    )
                )

        for lesson in _LESSONS:
            session.add(
                LessonRow(
                    spacecraft_id=SPACECRAFT_ID,
                    mission_id="ASTRIX-M00",
                    category=lesson["category"],
                    description=lesson["description"],
                    evidence=lesson["evidence"],
                    confidence=lesson["confidence"],
                    validation_status=lesson["validation_status"],
                    recurring=lesson["recurring"],
                    occurrences=lesson["occurrences"],
                )
            )

        session.commit()
    log.info("seeded structured mission memory")
    return True


def seed_vectors(vectors: VectorStore) -> int:
    """Populate vector memory with reports, procedures and case studies."""
    added = 0
    groups = [
        ("mission_report", _MISSION_REPORTS),
        ("procedure", _PROCEDURES),
        ("case_study", _CASE_STUDIES),
    ]
    for kind, entries in groups:
        for entry in entries:
            if vectors.has(entry["doc_id"]):
                continue
            vectors.add(
                VectorDoc(
                    doc_id=entry["doc_id"],
                    text=f"{entry['title']}. {entry['text']}",
                    metadata={
                        "kind": kind,
                        "title": entry["title"],
                        "mission_id": entry.get("mission_id", "reference"),
                        "spacecraft_id": SPACECRAFT_ID
                        if kind != "case_study"
                        else entry["title"].split(":")[-1].strip(),
                        "subsystem": entry.get("subsystem", "UNKNOWN"),
                        "failure_mode": entry.get("failure_mode"),
                        "recovery_action": entry.get("recovery_action"),
                        "outcome": entry.get("outcome", "UNKNOWN"),
                    },
                )
            )
            added += 1

    # Lessons are also retrievable descriptively, not only via SQL.
    for index, lesson in enumerate(_LESSONS):
        doc_id = f"seed-lesson-{index}"
        if vectors.has(doc_id):
            continue
        vectors.add(
            VectorDoc(
                doc_id=doc_id,
                text=f"Lesson learned ({lesson['category']}): {lesson['description']} "
                f"Evidence: {'; '.join(lesson['evidence'])}",
                metadata={
                    "kind": "lesson",
                    "title": f"Lesson: {lesson['category'].replace('_', ' ')}",
                    "mission_id": "ASTRIX-M00",
                    "spacecraft_id": SPACECRAFT_ID,
                    "outcome": "UNKNOWN",
                },
            )
        )
        added += 1

    if added:
        vectors.persist()
        log.info("seeded %d documents into vector memory", added)
    return added


def seed_all(session_factory: sessionmaker, vectors: VectorStore) -> dict:
    return {
        "structured_seeded": seed_structured(session_factory),
        "documents_added": seed_vectors(vectors),
    }
