"""Evidence rendering for agent prompts.

Agents never receive a raw telemetry dump. They receive a compact, unit-labelled
digest plus the retrieved mission history, because a) the interesting signal is
in relationships between channels, not in the absolute values, and b) a prompt
made of 40 unlabelled floats produces confident nonsense.

Every function here is pure text formatting — no reasoning, no thresholds.
"""

from __future__ import annotations

from ..core.schemas import (
    DetectionResult,
    Diagnosis,
    MemoryRecall,
    ResourceState,
    RiskAssessment,
    TelemetryFrame,
)

ASTRIX_ROLE = (
    "You are a reasoning agent inside ASTRIX, an autonomous spacecraft mission-intelligence "
    "system. You support flight operations; you do not command the spacecraft. Your output is "
    "checked afterwards by a deterministic safety-constraint engine and a digital-twin "
    "simulation, and high-risk actions require human approval.\n\n"
    "Rules you must follow:\n"
    "- Ground every claim in the telemetry or mission history you are given. Do not invent "
    "readings, missions or components.\n"
    "- Distinguish correlation from causation. Say 'associated with' unless the evidence "
    "establishes a mechanism.\n"
    "- If the evidence is ambiguous, say so and lower your confidence. An honest 0.55 is worth "
    "more than a false 0.95.\n"
    "- Be concise and specific. Cite the channel and the number."
)


def frame_summary(frame: TelemetryFrame) -> str:
    wheels = "\n".join(
        f"    wheel {n}: {rpm:>7.0f} rpm | vibration {vib:>5.2f} mm/s | current {cur:>4.2f} A"
        for n, rpm, vib, cur in zip(
            (1, 2, 3, 4), frame.wheel_rpms(), frame.wheel_vibrations(), frame.wheel_currents()
        )
    )
    return f"""CURRENT TELEMETRY  ({frame.spacecraft_id}, mission {frame.mission_id}, {frame.timestamp.isoformat()})

  CONTEXT
    operating mode: {frame.mode.value}
    eclipse: {frame.in_eclipse} | sun angle: {frame.sun_angle_deg:.1f}deg
    payload active: {frame.payload_active} | ground contact: {frame.ground_contact}

  POWER
    bus voltage: {frame.battery_voltage:.2f} V | current: {frame.battery_current:+.2f} A
    state of charge: {frame.state_of_charge:.1f}% | solar input: {frame.solar_power:.0f} W
    power balance: {frame.power_balance:+.1f} W

  THERMAL
    primary sensor: {frame.temperature:.1f} C | redundant sensor: {frame.temperature_secondary:.1f} C
    disagreement: {abs(frame.temperature - frame.temperature_secondary):.1f} C

  ATTITUDE
    pointing error: {frame.attitude_error_deg:.3f} deg
    gyro rates: x={frame.gyro_x:+.4f} y={frame.gyro_y:+.4f} z={frame.gyro_z:+.4f} deg/s
{wheels}

  COMMAND & DATA / COMMS
    cpu load: {frame.cpu_load:.0f}% | memory: {frame.memory_usage:.0f}%
    signal quality: {frame.communication_signal:.0f}% | packet loss: {frame.packet_loss:.2f}%
    downlink latency: {frame.downlink_latency_ms:.0f} ms

  PROPULSION
    thruster: {frame.thruster_status} | fuel: {frame.fuel_level:.1f}%"""


def detection_summary(detection: DetectionResult) -> str:
    factors = "\n".join(
        f"    {f.name:<24} value={f.value:.3f} weight={f.weight:.2f} -> {f.contribution:+.3f}  ({f.note})"
        for f in detection.context_factors
    )
    deviating = ", ".join(detection.deviating_parameters[:10]) or "none beyond 3 sigma"
    suppression = (
        f"\n  FALSE-ALARM SUPPRESSION APPLIED: {detection.suppression_reason}"
        if detection.suppressed
        else ""
    )
    return f"""DETECTION

  severity: {detection.severity.value}
  ML anomaly score: {detection.ml_score:.3f}
  context-adjusted risk score: {detection.final_score:.3f}
  elevated for: {detection.persistence_seconds:.0f} s
  channels beyond 3 sigma: {deviating}

  SCORE BREAKDOWN
{factors}{suppression}"""


def recall_summary(recall: MemoryRecall | None) -> str:
    if recall is None or (not recall.hits and not recall.lessons):
        return "MISSION MEMORY\n\n  No comparable historical event retrieved."

    lines = ["MISSION MEMORY"]
    if recall.hits:
        lines.append("\n  RETRIEVED RECORDS (most similar first)")
        for hit in recall.hits:
            lines.append(
                f"    [{hit.similarity:.2f}] {hit.title} ({hit.mission_id}, {hit.source})\n"
                f"           failure mode: {hit.failure_mode or 'n/a'} | "
                f"recovery: {hit.recovery_action or 'n/a'} | outcome: {hit.outcome.value}\n"
                f"           {hit.excerpt.strip()}"
            )
    if recall.lessons:
        lines.append("\n  LESSONS LEARNED ON RECORD")
        lines.extend(f"    - {lesson}" for lesson in recall.lessons)
    if recall.action_success_rates:
        lines.append("\n  MEASURED RECOVERY EFFECTIVENESS (from recorded outcomes)")
        lines.extend(
            f"    {action_id}: {rate:.2f}"
            for action_id, rate in sorted(
                recall.action_success_rates.items(), key=lambda kv: -kv[1]
            )
        )
    return "\n".join(lines)


def resource_summary(resources: ResourceState) -> str:
    return f"""AVAILABLE RESOURCES

  state of charge: {resources.state_of_charge:.1f}% | power margin: {resources.power_margin_w:+.1f} W
  fuel: {resources.fuel_level:.1f}%
  operational reaction wheels: {resources.operational_wheels} (3 required for 3-axis control)
  wheels without a degradation signature: {resources.healthy_wheels}
  thermal headroom: {resources.thermal_headroom_c:.1f} C | cpu headroom: {resources.cpu_headroom:.0f}%
  ground link available: {resources.link_available}
  3-axis attitude control available: {resources.attitude_control_available}"""


def diagnosis_summary(diagnosis: Diagnosis) -> str:
    evidence = "\n".join(f"    - {e}" for e in diagnosis.evidence) or "    - none recorded"
    return f"""DIAGNOSIS

  subsystem: {diagnosis.subsystem.value} | component: {diagnosis.component or 'unspecified'}
  probable cause: {diagnosis.probable_cause}
  failure mode: {diagnosis.failure_mode or 'unclassified'}
  confidence: {diagnosis.confidence:.2f} | historical match: {diagnosis.historical_match}
  evidence:
{evidence}"""


def risk_summary(risk: RiskAssessment) -> str:
    cascades = "\n".join(f"    - {c}" for c in risk.cascading_risks) or "    - none identified"
    time_to_impact = (
        f"{risk.time_to_impact_minutes:.0f} min" if risk.time_to_impact_minutes else "not estimated"
    )
    return f"""RISK ASSESSMENT

  severity: {risk.severity.value} | mission impact: {risk.mission_impact}
  power: {risk.power_risk} | attitude: {risk.attitude_risk} | thermal: {risk.thermal_risk} | comms: {risk.communication_risk}
  estimated time to mission impact: {time_to_impact}
  cascading risks:
{cascades}
  rationale: {risk.rationale}"""
