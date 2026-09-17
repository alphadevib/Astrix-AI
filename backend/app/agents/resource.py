"""Resource Agent (master context §8.6).

Answers the question the Recovery Agent cannot answer for itself: *can the
spacecraft afford this?* Pure computation over the current frame — no LLM, no
history. Feasibility is arithmetic, and arithmetic should not be delegated to a
language model.
"""

from __future__ import annotations

from ..core.schemas import ResourceState, TelemetryFrame

# A wheel below this speed is spun down, not merely slow.
WHEEL_OPERATIONAL_RPM = 50.0
# Vibration above this is the degradation signature from the failure-mode catalogue.
WHEEL_DEGRADED_VIBRATION = 1.2
THERMAL_CRITICAL_C = 55.0
# Link health thresholds. Note these are about the *subsystem*, not the orbit.
LINK_SIGNAL_MIN = 60.0
LINK_PACKET_LOSS_MAX = 10.0


class ResourceAgent:
    name = "resource"

    def run(self, frame: TelemetryFrame) -> ResourceState:
        rpms = frame.wheel_rpms()
        vibrations = frame.wheel_vibrations()

        operational = [n for n in (1, 2, 3, 4) if rpms[n - 1] > WHEEL_OPERATIONAL_RPM]
        healthy = [
            n for n in operational if vibrations[n - 1] < WHEEL_DEGRADED_VIBRATION
        ]

        return ResourceState(
            state_of_charge=frame.state_of_charge,
            power_margin_w=round(frame.power_balance, 2),
            fuel_level=frame.fuel_level,
            operational_wheels=operational,
            healthy_wheels=healthy,
            thermal_headroom_c=round(THERMAL_CRITICAL_C - frame.temperature, 2),
            cpu_headroom=round(100.0 - frame.cpu_load, 2),
            # `link_available` means the communications subsystem is HEALTHY, not
            # that the spacecraft is over a ground station right now. A LEO
            # satellite is out of contact for most of every orbit; that is normal
            # operations, not a failure, and treating it as one would strand the
            # spacecraft on monitoring-only actions ~90% of the time. The safety
            # rule that restricts behaviour on link loss (SR-004) is about a
            # *broken* link, which is what this flag reports.
            link_available=(
                frame.communication_signal >= LINK_SIGNAL_MIN
                and frame.packet_loss < LINK_PACKET_LOSS_MAX
            ),
        )
