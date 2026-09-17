"""Hardware hub: device state, baseline calibration, telemetry overlay, command queue."""

from __future__ import annotations

import itertools
import math
import re
import threading
import time
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Faults the firmware can inject into its own sensor path (see astrix_hil.ino).
FAULTS: dict[str, str] = {
    "TEMP_BIAS": "Thermistor reads high by up to 15 °C (sensor bias)",
    "SENSOR_STUCK": "Temperature channel freezes at its last value",
    "VIB_SPIKE": "Accelerometer vibration rises as a bearing would",
    "GYRO_DRIFT": "Gyro bias ramps on all axes",
    "BROWNOUT": "Bus voltage sags by up to 1.2 V",
    "OVERCURRENT": "Load current rises by up to 0.8 A",
    "MOTOR_STALL": "Wheel motor loses speed under load",
    "DROPOUT": "A fraction of telemetry packets is dropped",
}

ACTIONS: dict[str, str] = {
    "ISOLATE_WHEEL": "Power off the wheel motor",
    "RESTART_WHEEL": "Cycle the wheel motor",
    "WHEEL_SPEED": "Set wheel speed percent (arg 0-100)",
    "SAFE_MODE": "Minimum loads, status LED blinking",
    "NOMINAL_MODE": "Leave safe/power-save mode",
    "POWER_SAVE": "Shed non-essential loads",
    "REDUNDANT_SENSOR": "Switch to the backup temperature sensor",
    "RECAL_GYRO": "Zero the gyro bias",
}

COMMANDS: dict[str, str] = {
    "PING": "Liveness check",
    "STATUS": "Report mode, fault and actuator state",
    "CAL": "Zero the on-board sensor offsets",
    "RATE <hz>": "Telemetry rate, 1-20 Hz",
    "INJECT <FAULT> <severity 0-1>": "Inject an on-board fault",
    "CLEAR": "Clear the injected fault",
    "ACT <ACTION> [arg]": "Execute a recovery action on the hardware",
    "MOTOR <0-255>": "Raw wheel-motor PWM",
    "LED <0|1>": "Status LED",
}

_COMMAND_RE = re.compile(
    r"^(PING|STATUS|CAL|CLEAR"
    r"|RATE (?:[1-9]|1\d|20)"
    r"|INJECT (?:" + "|".join(FAULTS) + r") (?:0(?:\.\d+)?|1(?:\.0+)?)"
    r"|ACT (?:" + "|".join(ACTIONS) + r")(?: \d{1,3})?"
    r"|MOTOR (?:\d{1,3})"
    r"|LED [01])$"
)

# ASTRIX recovery action -> firmware command.
_ACTION_MAP = {
    "isolate_wheel_3": "ACT ISOLATE_WHEEL",
    "switch_redundant_wheel_config": "ACT ISOLATE_WHEEL",
    "restart_wheel_3": "ACT RESTART_WHEEL",
    "reduce_wheel_speed": "ACT WHEEL_SPEED 62",
    "enter_safe_mode": "ACT SAFE_MODE",
    "enter_power_save": "ACT POWER_SAVE",
    "reduce_payload_duty_cycle": "ACT POWER_SAVE",
    "switch_to_redundant_sensor": "ACT REDUNDANT_SENSOR",
    "recalibrate_gyro_bias": "ACT RECAL_GYRO",
}


def action_to_command(action_id: str) -> str | None:
    return _ACTION_MAP.get(action_id)


def validate_command(command: str) -> str:
    normalised = " ".join(command.strip().upper().split())
    if not _COMMAND_RE.match(normalised):
        raise ValueError(f"'{command}' is not a valid device command")
    return normalised


class HardwareReading(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dev: str = Field(default="astrix-hil", max_length=40)
    fw: str = Field(default="?", max_length=16)
    seq: int = 0
    ms: int = 0
    temp_c: float | None = Field(default=None, ge=-60.0, le=150.0)
    bus_v: float | None = Field(default=None, ge=0.0, le=60.0)
    current_a: float | None = Field(default=None, ge=-20.0, le=20.0)
    light: float | None = Field(default=None, ge=0.0, le=1.0)
    vib_g: float | None = Field(default=None, ge=0.0, le=16.0)
    gyro: list[float] | None = Field(default=None, min_length=3, max_length=3)
    rpm: float | None = Field(default=None, ge=0.0, le=100_000.0)
    fault: str = Field(default="NONE", max_length=24)
    sev: float = Field(default=0.0, ge=0.0, le=1.0)
    mode: str = Field(default="NOMINAL", max_length=24)


_NUMERIC = ("temp_c", "bus_v", "current_a", "light", "vib_g", "rpm")


class HardwareHub:
    CALIBRATION_SAMPLES = 10

    def __init__(self, bus=None, stale_seconds: float = 5.0) -> None:
        self.bus = bus
        self.stale_seconds = stale_seconds
        self.overlay_enabled = True
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.device: dict[str, Any] | None = None
            self.transport: str | None = None
            self.latest: HardwareReading | None = None
            self.last_seen = 0.0
            self.readings: deque[dict[str, Any]] = deque(maxlen=300)
            self.outbox: deque[dict[str, Any]] = deque(maxlen=100)
            self.log: deque[dict[str, Any]] = deque(maxlen=200)
            self.baseline: dict[str, Any] | None = None
            self._cal: list[HardwareReading] = []
            self.received = 0
            self._last_publish = 0.0

    # -- ingest ------------------------------------------------------------- #

    @property
    def connected(self) -> bool:
        return self.latest is not None and time.monotonic() - self.last_seen < self.stale_seconds

    def ingest(self, readings: list[HardwareReading], transport: str = "web-serial", acks: list[str] | None = None) -> dict:
        now = time.monotonic()
        with self._lock:
            for reading in readings:
                self.latest = reading
                self.received += 1
                self.readings.append({**reading.model_dump(), "t": time.time()})
                if self.baseline is None and reading.fault == "NONE" and reading.mode == "NOMINAL":
                    self._cal.append(reading)
                    if len(self._cal) >= self.CALIBRATION_SAMPLES:
                        self.baseline = self._mean(self._cal)
                        self._cal = []
            if readings:
                self.last_seen = now
                self.transport = transport
                last = readings[-1]
                self.device = {"dev": last.dev, "fw": last.fw}
            for ack in acks or []:
                self.log.append({"direction": "rx", "text": str(ack)[:200], "at": time.time()})
            commands = list(self.outbox)
            self.outbox.clear()
            for cmd in commands:
                self.log.append({"direction": "tx", "text": cmd["command"], "source": cmd["source"], "at": time.time()})
        if self.bus is not None and readings and now - self._last_publish > 0.5:
            self._last_publish = now
            self.bus.publish("hardware", {"reading": readings[-1].model_dump(), "calibrated": self.baseline is not None})
        return {"accepted": len(readings), "commands": [c["command"] for c in commands], "calibrated": self.baseline is not None}

    @staticmethod
    def _mean(samples: list[HardwareReading]) -> dict[str, Any]:
        base: dict[str, Any] = {}
        for name in _NUMERIC:
            values = [getattr(s, name) for s in samples if getattr(s, name) is not None]
            base[name] = sum(values) / len(values) if values else None
        gyros = [s.gyro for s in samples if s.gyro]
        base["gyro"] = [sum(g[i] for g in gyros) / len(gyros) for i in range(3)] if gyros else None
        return base

    def recalibrate(self) -> None:
        with self._lock:
            self.baseline = None
            self._cal = []

    # -- commands ----------------------------------------------------------- #

    def queue(self, command: str, source: str = "operator") -> dict[str, Any]:
        normalised = validate_command(command)
        item = {"id": next(self._ids), "command": normalised, "source": source, "at": time.time()}
        with self._lock:
            self.outbox.append(item)
        if self.bus is not None:
            self.bus.publish("hardware_command", item)
        return item

    def record_local(self, command: str, source: str = "operator") -> dict[str, Any]:
        """Log a command the browser already wrote to the port itself."""
        normalised = validate_command(command)
        entry = {"direction": "tx", "text": normalised, "source": source, "at": time.time()}
        with self._lock:
            self.log.append(entry)
        return entry

    # -- overlay ------------------------------------------------------------ #

    def overlay(self, frame):
        """Blend the latest reading into a simulated TelemetryFrame as deviations."""
        if not self.overlay_enabled or not self.connected or self.baseline is None or self.latest is None:
            return frame
        r, b = self.latest, self.baseline
        update: dict[str, Any] = {}

        def delta(name: str) -> float | None:
            value, ref = getattr(r, name), b.get(name)
            return None if value is None or ref is None else value - ref

        if (d := delta("temp_c")) is not None:
            update["temperature"] = round(frame.temperature + d, 2)
        if (d := delta("bus_v")) is not None:
            update["battery_voltage"] = round(max(18.0, frame.battery_voltage + 5.6 * d), 3)
        if (d := delta("current_a")) is not None:
            update["battery_current"] = round(frame.battery_current - 4.0 * d, 3)
        if r.light is not None and b.get("light") and b["light"] > 0.05 and frame.solar_power > 0:
            update["solar_power"] = round(frame.solar_power * min(1.6, max(0.0, r.light / b["light"])), 2)
        if (d := delta("vib_g")) is not None and d > 0:
            update["wheel_3_vibration"] = round(frame.wheel_3_vibration + 2.5 * d, 4)
            update["wheel_3_current"] = round(frame.wheel_3_current + 0.35 * d, 4)
        if r.rpm is not None and b.get("rpm"):
            update["wheel_3_rpm"] = round(frame.wheel_3_rpm * max(0.0, r.rpm / b["rpm"]), 1)
        if r.gyro and b.get("gyro"):
            dg = [r.gyro[i] - b["gyro"][i] for i in range(3)]
            update["gyro_x"] = round(frame.gyro_x + 0.02 * dg[0], 5)
            update["gyro_y"] = round(frame.gyro_y + 0.02 * dg[1], 5)
            update["gyro_z"] = round(frame.gyro_z + 0.02 * dg[2], 5)
            update["attitude_error_deg"] = round(
                frame.attitude_error_deg + 0.01 * math.sqrt(sum(x * x for x in dg)), 4
            )
        if r.fault != "NONE" and frame.injected_fault is None:
            update["injected_fault"] = f"HIL: {r.fault.replace('_', ' ').lower()}"
        return frame.model_copy(update=update) if update else frame

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self.connected,
                "transport": self.transport if self.connected else None,
                "device": self.device,
                "latest": self.latest.model_dump() if self.latest else None,
                "received": self.received,
                "calibrated": self.baseline is not None,
                "calibration_progress": len(self._cal),
                "calibration_samples": self.CALIBRATION_SAMPLES,
                "baseline": self.baseline,
                "overlay_enabled": self.overlay_enabled,
                "pending_commands": len(self.outbox),
                "log": list(self.log)[-40:],
                "faults": FAULTS,
                "actions": ACTIONS,
                "commands": COMMANDS,
            }

    def history(self, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.readings)[-limit:]
