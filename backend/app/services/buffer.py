"""Rolling telemetry window and persistence tracking.

Persistence — how long a deviation has been present — is one of the six terms in
the context-aware score, and it is the cheapest one to get wrong. A single noisy
frame must not accumulate persistence, and a fault that clears must not keep it.
So a streak is tracked in *simulated spacecraft time* (frame timestamps), not
wall-clock, and resets the moment a frame comes back inside the band.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from ..core.schemas import TelemetryFrame


@dataclass
class _Track:
    frames: deque[TelemetryFrame]
    streak_start: datetime | None = None
    last_seen: datetime | None = None
    validation_errors: list[str] = field(default_factory=list)


class TelemetryBuffer:
    """Per-spacecraft rolling window. Not shared across spacecraft."""

    def __init__(self, maxlen: int = 180) -> None:
        self.maxlen = maxlen
        self._tracks: dict[str, _Track] = {}

    def _track(self, spacecraft_id: str) -> _Track:
        track = self._tracks.get(spacecraft_id)
        if track is None:
            track = _Track(frames=deque(maxlen=self.maxlen))
            self._tracks[spacecraft_id] = track
        return track

    # -- ingest ------------------------------------------------------------- #

    def add(self, frame: TelemetryFrame) -> int:
        track = self._track(frame.spacecraft_id)
        track.frames.append(frame)
        track.last_seen = frame.timestamp
        return len(track.frames)

    def validate(self, frame: TelemetryFrame) -> list[str]:
        """Telemetry Agent validation (§8.1): physically impossible values.

        Range violations are *reported*, not corrected — a sensor reading 150%
        state of charge is itself the anomaly, and silently clamping it would hide
        exactly the fault we exist to catch.
        """
        errors: list[str] = []
        checks = [
            ("state_of_charge", frame.state_of_charge, 0.0, 105.0),
            ("battery_voltage", frame.battery_voltage, 0.0, 60.0),
            ("fuel_level", frame.fuel_level, 0.0, 100.0),
            ("cpu_load", frame.cpu_load, 0.0, 100.0),
            ("memory_usage", frame.memory_usage, 0.0, 100.0),
            ("packet_loss", frame.packet_loss, 0.0, 100.0),
            ("communication_signal", frame.communication_signal, 0.0, 100.0),
            ("temperature", frame.temperature, -150.0, 200.0),
        ]
        for name, value, low, high in checks:
            if not (low <= value <= high):
                errors.append(f"{name}={value} outside the physically possible range [{low}, {high}]")
        for index, rpm in enumerate(frame.wheel_rpms(), start=1):
            if not (-12000.0 <= rpm <= 12000.0):
                errors.append(f"wheel_{index}_rpm={rpm} outside the mechanical limit")
        return errors

    # -- persistence -------------------------------------------------------- #

    def update_persistence(
        self, spacecraft_id: str, frame: TelemetryFrame, elevated: bool
    ) -> float:
        """Seconds the spacecraft has been continuously elevated. 0 if not."""
        track = self._track(spacecraft_id)
        if not elevated:
            track.streak_start = None
            return 0.0
        if track.streak_start is None:
            track.streak_start = frame.timestamp
            return 0.0
        return max(0.0, (frame.timestamp - track.streak_start).total_seconds())

    def clear(self, spacecraft_id: str) -> None:
        """Drop the window and streak for a spacecraft (new telemetry session)."""
        self._tracks.pop(spacecraft_id, None)

    def reset_persistence(self, spacecraft_id: str) -> None:
        """Called after a recovery executes: the old streak is no longer meaningful."""
        self._track(spacecraft_id).streak_start = None

    # -- read --------------------------------------------------------------- #

    def window(self, spacecraft_id: str) -> list[TelemetryFrame]:
        return list(self._track(spacecraft_id).frames)

    def latest(self, spacecraft_id: str) -> TelemetryFrame | None:
        frames = self._track(spacecraft_id).frames
        return frames[-1] if frames else None

    def history(self, spacecraft_id: str, limit: int = 120) -> list[TelemetryFrame]:
        frames = self._track(spacecraft_id).frames
        return list(frames)[-limit:]

    def spacecraft_ids(self) -> list[str]:
        return list(self._tracks)

    def size(self, spacecraft_id: str) -> int:
        return len(self._track(spacecraft_id).frames)
