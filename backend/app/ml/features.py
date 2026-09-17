"""Feature engineering for the anomaly detector.

The derived features at the bottom matter more than they look: a single wheel
degrading barely moves any individual RPM reading, but it moves `wheel_rpm_spread`
and `wheel_vibration_max` immediately. Cross-sensor deltas (`temp_sensor_delta`)
are what let the Diagnostic Agent separate "the bus is hot" from "one sensor lies".
"""

from __future__ import annotations

import numpy as np

from ..core.schemas import WHEELS, TelemetryFrame

RAW_FEATURES: tuple[str, ...] = (
    "battery_voltage",
    "battery_current",
    "state_of_charge",
    "solar_power",
    "temperature",
    "temperature_secondary",
    "gyro_x",
    "gyro_y",
    "gyro_z",
    *[f"wheel_{n}_rpm" for n in WHEELS],
    *[f"wheel_{n}_vibration" for n in WHEELS],
    *[f"wheel_{n}_current" for n in WHEELS],
    "attitude_error_deg",
    # fuel_level is deliberately absent: it only ever decreases over a mission, so
    # any learned "nominal" range is outgrown within hours and every frame would
    # look anomalous. Propellant is guarded by the safety engine's reserve rule.
    "cpu_load",
    "memory_usage",
    "communication_signal",
    "packet_loss",
    "downlink_latency_ms",
)

DERIVED_FEATURES: tuple[str, ...] = (
    "power_balance",
    "wheel_rpm_spread",
    "wheel_vibration_max",
    "wheel_vibration_spread",
    "wheel_current_max",
    "gyro_magnitude",
    "temp_sensor_delta",
)

FEATURE_NAMES: tuple[str, ...] = RAW_FEATURES + DERIVED_FEATURES


def derived_values(frame: TelemetryFrame) -> dict[str, float]:
    rpms = frame.wheel_rpms()
    vibs = frame.wheel_vibrations()
    currents = frame.wheel_currents()
    return {
        "power_balance": frame.power_balance,
        "wheel_rpm_spread": max(rpms) - min(rpms),
        "wheel_vibration_max": max(vibs),
        "wheel_vibration_spread": max(vibs) - min(vibs),
        "wheel_current_max": max(currents),
        "gyro_magnitude": float(
            np.sqrt(frame.gyro_x**2 + frame.gyro_y**2 + frame.gyro_z**2)
        ),
        "temp_sensor_delta": abs(frame.temperature - frame.temperature_secondary),
    }


def frame_to_vector(frame: TelemetryFrame) -> np.ndarray:
    derived = derived_values(frame)
    values = [float(getattr(frame, name)) for name in RAW_FEATURES]
    values += [derived[name] for name in DERIVED_FEATURES]
    return np.asarray(values, dtype=np.float64)


def frames_to_matrix(frames: list[TelemetryFrame]) -> np.ndarray:
    if not frames:
        return np.empty((0, len(FEATURE_NAMES)), dtype=np.float64)
    return np.vstack([frame_to_vector(f) for f in frames])
