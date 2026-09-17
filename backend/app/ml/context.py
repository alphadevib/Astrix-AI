"""Context-aware anomaly scoring (master context §4.1 and §18).

This module is the answer to "how do you reduce false alarms". A raw Isolation
Forest score says only *this telemetry is unusual*. That is not enough: a bus
temperature of 42 °C is alarming at night with the payload off and completely
expected in direct sunlight with a high-power payload running.

So the final risk score is a weighted combination of six terms:

    final = w1·ml_score
          + w2·context_deviation      (excursion beyond the *mode-conditioned* envelope)
          + w3·subsystem_correlation  (do independent sensors corroborate?)
          + w4·historical_similarity  (have we seen this before, and was it benign?)
          + w5·persistence            (transient spike vs. sustained trend)
          + w6·mission_impact         (does it threaten the mission right now?)

Each term is stored on the result as a `ContextFactor`, so the dashboard can
always show which term moved the score — the scoring is auditable, not a
black box bolted on top of another black box.
"""

from __future__ import annotations

from ..config import Settings
from ..core.enums import OperatingMode, Severity, Subsystem
from ..core.schemas import ContextFactor, DetectionResult, MemoryRecall, TelemetryFrame

# --------------------------------------------------------------------------- #
# Mode-conditioned nominal envelopes
# --------------------------------------------------------------------------- #
# (low, high) bounds that are *normal for that operating mode*. Being outside the
# NOMINAL envelope while in PAYLOAD_ACTIVE mode is not an anomaly — it is the
# payload doing its job. This table is the single biggest false-alarm reducer.

MODE_ENVELOPES: dict[OperatingMode, dict[str, tuple[float, float]]] = {
    OperatingMode.NOMINAL: {
        "temperature": (8.0, 36.0),
        "solar_power": (170.0, 270.0),
        "cpu_load": (12.0, 55.0),
        "battery_current": (-7.0, 4.0),
        "battery_voltage": (26.9, 29.2),
        "state_of_charge": (55.0, 100.0),
        "attitude_error_deg": (0.0, 0.20),
        "communication_signal": (80.0, 100.0),
    },
    OperatingMode.PAYLOAD_ACTIVE: {
        # Wide thermal + CPU envelope: this is the 42 °C-is-fine case.
        "temperature": (16.0, 49.0),
        "solar_power": (150.0, 270.0),
        "cpu_load": (45.0, 94.0),
        "battery_current": (-16.0, 2.0),
        "battery_voltage": (26.9, 29.2),
        "state_of_charge": (40.0, 100.0),
        "attitude_error_deg": (0.0, 0.25),
        "communication_signal": (75.0, 100.0),
    },
    OperatingMode.ECLIPSE: {
        # No sun: low temperature and zero generation are expected, not faults.
        "temperature": (-12.0, 26.0),
        "solar_power": (0.0, 14.0),
        "cpu_load": (10.0, 55.0),
        "battery_current": (-13.0, 0.5),
        "battery_voltage": (26.9, 29.2),
        "state_of_charge": (35.0, 100.0),
        "attitude_error_deg": (0.0, 0.22),
        "communication_signal": (75.0, 100.0),
    },
    OperatingMode.COMMS_PASS: {
        "temperature": (10.0, 42.0),
        "solar_power": (150.0, 270.0),
        "cpu_load": (28.0, 75.0),
        "battery_current": (-12.0, 2.0),
        "battery_voltage": (26.9, 29.2),
        "state_of_charge": (45.0, 100.0),
        "attitude_error_deg": (0.0, 0.18),
        "communication_signal": (65.0, 100.0),
    },
    OperatingMode.MANEUVER: {
        # Attitude rates and errors are legitimately large while slewing.
        "temperature": (8.0, 42.0),
        "solar_power": (60.0, 270.0),
        "cpu_load": (30.0, 80.0),
        "battery_current": (-18.0, 2.0),
        "battery_voltage": (26.9, 29.2),
        "state_of_charge": (40.0, 100.0),
        "attitude_error_deg": (0.0, 3.0),
        "communication_signal": (55.0, 100.0),
    },
    OperatingMode.SAFE_MODE: {
        "temperature": (-8.0, 32.0),
        "solar_power": (0.0, 270.0),
        "cpu_load": (4.0, 28.0),
        "battery_current": (-8.0, 4.0),
        "battery_voltage": (26.9, 29.2),
        "state_of_charge": (15.0, 100.0),
        "attitude_error_deg": (0.0, 1.5),
        "communication_signal": (40.0, 100.0),
    },
}

# --------------------------------------------------------------------------- #
# Cross-sensor corroboration
# --------------------------------------------------------------------------- #
# A real subsystem fault shows up on several *physically independent* channels.
# One channel alone is far more likely to be a sensor glitch or a benign
# transient, so a lone deviation scores low correlation and gets suppressed.

# parameter-name prefix -> subsystem it implicates
_PARAM_SUBSYSTEM: tuple[tuple[str, Subsystem], ...] = (
    ("wheel_", Subsystem.ADCS),
    ("gyro_", Subsystem.ADCS),
    ("attitude_", Subsystem.ADCS),
    ("battery_", Subsystem.POWER),
    ("state_of_charge", Subsystem.POWER),
    ("solar_power", Subsystem.POWER),
    ("power_balance", Subsystem.POWER),
    ("temperature", Subsystem.THERMAL),
    ("temp_sensor_delta", Subsystem.THERMAL),
    ("cpu_load", Subsystem.CDH),
    ("memory_usage", Subsystem.CDH),
    ("communication_signal", Subsystem.COMMS),
    ("packet_loss", Subsystem.COMMS),
    ("downlink_latency_ms", Subsystem.COMMS),
    ("fuel_level", Subsystem.PROPULSION),
    ("thruster", Subsystem.PROPULSION),
)


def implicated_subsystem(deviating: list[str]) -> Subsystem:
    """Most-implicated subsystem, by count of deviating channels."""
    tally: dict[Subsystem, int] = {}
    for param in deviating:
        for prefix, subsystem in _PARAM_SUBSYSTEM:
            if param.startswith(prefix):
                tally[subsystem] = tally.get(subsystem, 0) + 1
                break
    if not tally:
        return Subsystem.UNKNOWN
    return max(tally.items(), key=lambda kv: kv[1])[0]


def _corroboration(subsystem: Subsystem, frame: TelemetryFrame) -> tuple[float, list[str]]:
    """Fraction of that subsystem's independent checks that agree something is wrong."""
    vibs = frame.wheel_vibrations()
    currents = frame.wheel_currents()
    rpms = frame.wheel_rpms()

    checks: list[tuple[str, bool]]
    if subsystem is Subsystem.ADCS:
        checks = [
            ("wheel vibration elevated", max(vibs) > 1.2),
            ("wheel motor current elevated", max(currents) > 0.95),
            ("wheel RPM spread abnormal", (max(rpms) - min(rpms)) > 400.0),
            ("pointing error growing", frame.attitude_error_deg > 0.30),
        ]
    elif subsystem is Subsystem.POWER:
        checks = [
            ("bus voltage low", frame.battery_voltage < 26.5),
            ("discharge rate high", frame.battery_current < -11.0),
            ("state of charge low", frame.state_of_charge < 45.0),
            ("negative power balance", frame.power_balance < -25.0),
        ]
    elif subsystem is Subsystem.THERMAL:
        checks = [
            ("primary sensor hot", frame.temperature > 46.0),
            # Both sensors agreeing is *evidence the heat is real*; a large
            # disagreement points at a sensor fault instead of a thermal fault.
            ("redundant sensor agrees", abs(frame.temperature - frame.temperature_secondary) < 3.0),
            ("thermal load present", frame.cpu_load > 75.0 or frame.payload_active),
            ("no sun-driven explanation", not frame.in_eclipse and frame.sun_angle_deg < 45.0),
        ]
    elif subsystem is Subsystem.COMMS:
        checks = [
            ("packet loss elevated", frame.packet_loss > 2.0),
            ("signal quality degraded", frame.communication_signal < 80.0),
            ("latency elevated", frame.downlink_latency_ms > 1200.0),
            ("contact expected now", frame.ground_contact),
        ]
    elif subsystem is Subsystem.CDH:
        checks = [
            ("CPU saturated", frame.cpu_load > 90.0),
            ("memory pressure", frame.memory_usage > 85.0),
            ("thermal coupling visible", frame.temperature > 40.0),
        ]
    elif subsystem is Subsystem.PROPULSION:
        checks = [
            ("fuel below plan", frame.fuel_level < 25.0),
            ("thruster commanded", frame.thruster_status != "OFF"),
        ]
    else:
        return 0.0, []

    fired = [name for name, ok in checks if ok]
    return len(fired) / len(checks), fired


# --------------------------------------------------------------------------- #
# Scorer
# --------------------------------------------------------------------------- #

PERSISTENCE_SATURATION_SECONDS = 300.0


class ContextScorer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    # -- individual terms --------------------------------------------------- #

    def _context_deviation(self, frame: TelemetryFrame) -> tuple[float, str]:
        """Worst relative excursion beyond the envelope for the *current* mode."""
        envelope = MODE_ENVELOPES.get(frame.mode, MODE_ENVELOPES[OperatingMode.NOMINAL])
        worst = 0.0
        worst_note = "all channels inside the envelope for this operating mode"
        for param, (lo, hi) in envelope.items():
            value = float(getattr(frame, param))
            span = max(hi - lo, 1e-6)
            excursion = max(0.0, lo - value, value - hi) / span
            if excursion > worst:
                worst = excursion
                worst_note = (
                    f"{param}={value:.2f} outside {frame.mode.value} envelope "
                    f"[{lo:.1f}, {hi:.1f}]"
                )
        return min(worst, 1.0), worst_note

    def _historical_similarity(self, recall: MemoryRecall | None) -> tuple[float, str]:
        if recall is None or not recall.hits:
            return 0.0, "no comparable historical event retrieved"
        best = recall.hits[0]
        return (
            recall.best_similarity,
            f"closest match: {best.title} ({best.mission_id}, outcome {best.outcome.value})",
        )

    def _mission_impact(self, frame: TelemetryFrame, subsystem: Subsystem) -> tuple[float, str]:
        impact = 0.1
        notes: list[str] = []
        if frame.state_of_charge < 35.0:
            impact = max(impact, 0.9)
            notes.append("battery state of charge critically low")
        elif frame.state_of_charge < 50.0:
            impact = max(impact, 0.6)
            notes.append("battery margin reduced")
        if frame.attitude_error_deg > 1.0 and frame.mode is not OperatingMode.MANEUVER:
            impact = max(impact, 0.85)
            notes.append("pointing budget exceeded — payload and link both at risk")
        if subsystem is Subsystem.ADCS and frame.payload_active:
            impact = max(impact, 0.7)
            notes.append("attitude fault during payload operation")
        if frame.mode is OperatingMode.SAFE_MODE:
            impact = max(impact, 0.8)
            notes.append("spacecraft already in safe mode")
        if frame.communication_signal < 70.0 or frame.packet_loss > 4.0:
            # Degraded link, not merely "between ground passes" — being out of
            # contact is normal for most of a LEO orbit and is not a risk factor.
            impact = max(impact, 0.5)
            notes.append("degraded ground link limits recovery options")
        return impact, "; ".join(notes) or "no immediate mission-level consequence"

    # -- main --------------------------------------------------------------- #

    def score(
        self,
        frame: TelemetryFrame,
        ml_score: float,
        deviating_parameters: list[str],
        recall: MemoryRecall | None = None,
        persistence_seconds: float = 0.0,
        window_size: int = 0,
        previous_severity: Severity | None = None,
    ) -> DetectionResult:
        subsystem = implicated_subsystem(deviating_parameters)

        deviation, deviation_note = self._context_deviation(frame)
        correlation, fired = _corroboration(subsystem, frame)
        similarity, similarity_note = self._historical_similarity(recall)
        persistence = min(persistence_seconds / PERSISTENCE_SATURATION_SECONDS, 1.0)
        impact, impact_note = self._mission_impact(frame, subsystem)

        factors = [
            ContextFactor(
                name="ml_anomaly_score",
                value=ml_score,
                weight=self.s.weight_ml_score,
                note=f"Isolation Forest, {len(deviating_parameters)} channel(s) beyond 3 sigma",
            ),
            ContextFactor(
                name="context_deviation",
                value=deviation,
                weight=self.s.weight_context_deviation,
                note=deviation_note,
            ),
            ContextFactor(
                name="subsystem_correlation",
                value=correlation,
                weight=self.s.weight_subsystem_correlation,
                note=(
                    f"{subsystem.value}: " + (", ".join(fired) if fired else "no corroborating channel")
                ),
            ),
            ContextFactor(
                name="historical_similarity",
                value=similarity,
                weight=self.s.weight_historical_similarity,
                note=similarity_note,
            ),
            ContextFactor(
                name="persistence",
                value=persistence,
                weight=self.s.weight_persistence,
                note=f"elevated for {persistence_seconds:.0f}s",
            ),
            ContextFactor(
                name="mission_impact",
                value=impact,
                weight=self.s.weight_mission_impact,
                note=impact_note,
            ),
        ]

        total_weight = sum(f.weight for f in factors) or 1.0
        final = sum(f.value * f.weight for f in factors) / total_weight
        final = float(min(max(final, 0.0), 1.0))

        # --- severity classification with hysteresis ---------------------------
        # A score resting near a cut point would otherwise flip level on every
        # frame, strobing the operator display and repeatedly re-opening and
        # closing the same anomaly. Escalating requires clearing the threshold by
        # the hysteresis margin; de-escalating requires falling below it by the
        # same margin. In between, the current level holds.
        watch, warning, critical = self.s.severity_cutpoints
        bands = (
            (Severity.CRITICAL, critical),
            (Severity.WARNING, warning),
            (Severity.WATCH, watch),
        )

        def classify(margin: float) -> Severity:
            for level, threshold in bands:
                if final >= threshold + margin:
                    return level
            return Severity.NORMAL

        margin = self.s.severity_hysteresis
        previous = previous_severity or Severity.NORMAL
        escalate_to = classify(+margin)
        deescalate_to = classify(-margin)

        if escalate_to.rank > previous.rank:
            severity = escalate_to
        elif deescalate_to.rank < previous.rank:
            severity = deescalate_to
        else:
            severity = previous

        # --- explicit false-alarm suppression --------------------------------
        # The ML model is shouting, but nothing is outside the envelope for this
        # mode, no independent sensor agrees, and history says this is routine.
        # Downgrade rather than page an operator — and say so on the record.
        suppressed = False
        suppression_reason: str | None = None
        benign_history = bool(
            recall
            and recall.hits
            and recall.best_similarity > 0.4
            and all(h.outcome.value in ("SUCCESSFUL", "UNKNOWN") for h in recall.hits[:2])
        )
        if (
            ml_score >= 0.55
            and deviation < 0.12
            and correlation <= 0.25
            and severity.rank < Severity.CRITICAL.rank
        ):
            suppressed = True
            severity = Severity.WATCH if severity.rank >= Severity.WATCH.rank else severity
            suppression_reason = (
                f"ML score {ml_score:.2f} not corroborated: every channel is inside the "
                f"{frame.mode.value} envelope and no independent sensor in {subsystem.value} agrees"
                + (" (historically benign)" if benign_history else "")
            )

        return DetectionResult(
            spacecraft_id=frame.spacecraft_id,
            timestamp=frame.timestamp,
            ml_score=round(ml_score, 4),
            final_score=round(final, 4),
            severity=severity,
            is_anomaly=severity.rank >= Severity.WATCH.rank,
            deviating_parameters=deviating_parameters,
            context_factors=factors,
            persistence_seconds=persistence_seconds,
            suppressed=suppressed,
            suppression_reason=suppression_reason,
            window_size=window_size,
        )
