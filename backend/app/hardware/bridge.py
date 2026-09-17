"""Headless serial bridge between an Astrix HIL board and the ASTRIX API.

    python -m backend.app.hardware.bridge --port COM3             # Windows
    python -m backend.app.hardware.bridge --port /dev/ttyACM0     # Linux
    python -m backend.app.hardware.bridge --emulate               # no hardware

Reads newline-delimited JSON from the board, batches readings to
`POST /hardware/telemetry`, and writes any commands the API returns back to the
board. `--emulate` runs a software stand-in with the same protocol, useful for
CI and for trying the flow before the board arrives.

The browser dashboard can do the same over Web Serial; use one or the other.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time

import httpx


class EmulatedBoard:
    """Software twin of astrix_hil.ino: same readings, faults and commands."""

    def __init__(self, seed: int = 3) -> None:
        self.rng = random.Random(seed)
        self.seq = 0
        self.start = time.monotonic()
        self.fault = "NONE"
        self.sev = 0.0
        self.fault_t = 0.0
        self.mode = "NOMINAL"
        self.motor = 180
        self.stuck_temp: float | None = None
        self.gyro_bias = [0.0, 0.0, 0.0]
        self.pending: list[str] = []

    def write(self, line: str) -> None:
        parts = line.strip().upper().split()
        if not parts:
            return
        cmd, args = parts[0], parts[1:]
        if cmd == "INJECT" and len(args) == 2:
            self.fault, self.sev, self.fault_t = args[0], float(args[1]), time.monotonic()
            self.stuck_temp = None
        elif cmd == "CLEAR":
            self.fault, self.sev, self.stuck_temp = "NONE", 0.0, None
        elif cmd == "ACT" and args:
            action = args[0]
            if action == "ISOLATE_WHEEL":
                self.motor = 0
                if self.fault in ("VIB_SPIKE", "MOTOR_STALL"):
                    self.fault = "NONE"
            elif action == "RESTART_WHEEL":
                self.motor = 180
            elif action == "WHEEL_SPEED" and len(args) > 1:
                self.motor = int(255 * int(args[1]) / 100)
            elif action == "SAFE_MODE":
                self.mode = "SAFE"
            elif action == "POWER_SAVE":
                self.mode = "POWER_SAVE"
            elif action == "NOMINAL_MODE":
                self.mode = "NOMINAL"
            elif action in ("REDUNDANT_SENSOR",) and self.fault in ("TEMP_BIAS", "SENSOR_STUCK"):
                self.fault = "NONE"
            elif action == "RECAL_GYRO" and self.fault == "GYRO_DRIFT":
                self.fault = "NONE"
        elif cmd == "MOTOR" and args:
            self.motor = max(0, min(255, int(args[0])))
        self.pending.append(json.dumps({"ack": " ".join(parts), "ok": True}))

    def read(self) -> dict | None:
        self.seq += 1
        t = time.monotonic() - self.start
        ramp = min(1.0, (time.monotonic() - self.fault_t) / 20.0) * self.sev if self.fault != "NONE" else 0.0
        load = 0.35 if self.mode == "SAFE" else 0.42 if self.mode == "POWER_SAVE" else 0.55
        temp = 24.0 + 0.6 * math.sin(t / 30.0) + self.rng.gauss(0, 0.05) + 3.0 * self.motor / 255
        if self.fault == "TEMP_BIAS":
            temp += 15.0 * ramp
        if self.fault == "SENSOR_STUCK":
            self.stuck_temp = self.stuck_temp if self.stuck_temp is not None else temp
            temp = self.stuck_temp
        vib = 0.08 + 0.12 * self.motor / 255 + abs(self.rng.gauss(0, 0.01))
        if self.fault == "VIB_SPIKE" and self.motor > 0:
            vib += 1.6 * ramp
        rpm = 4200.0 * self.motor / 255 + self.rng.gauss(0, 15)
        if self.fault == "MOTOR_STALL":
            rpm *= 1.0 - 0.8 * ramp
            load += 0.3 * ramp
        if self.fault == "GYRO_DRIFT":
            self.gyro_bias = [4.0 * ramp, -3.0 * ramp, 2.0 * ramp]
        else:
            self.gyro_bias = [0.0, 0.0, 0.0]
        current = load + 0.25 * self.motor / 255 + self.rng.gauss(0, 0.005)
        if self.fault == "OVERCURRENT":
            current += 0.8 * ramp
        bus_v = 5.02 - 0.08 * current + self.rng.gauss(0, 0.003)
        if self.fault == "BROWNOUT":
            bus_v -= 1.2 * ramp
        if self.fault == "DROPOUT" and self.rng.random() < self.sev * 0.8:
            return None
        return {
            "dev": "astrix-hil-emulator",
            "fw": "1.0.0",
            "seq": self.seq,
            "ms": int(t * 1000),
            "temp_c": round(temp, 2),
            "bus_v": round(max(0.0, bus_v), 3),
            "current_a": round(max(0.0, current), 3),
            "light": round(0.72 + 0.02 * math.sin(t / 50.0), 3),
            "vib_g": round(vib, 3),
            "gyro": [round(b + self.rng.gauss(0, 0.05), 3) for b in self.gyro_bias],
            "rpm": round(max(0.0, rpm), 1),
            "fault": self.fault,
            "sev": self.sev,
            "mode": self.mode,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Astrix HIL serial bridge")
    parser.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--emulate", action="store_true", help="use the software board instead of a serial port")
    parser.add_argument("--api", default=os.environ.get("ASTRIX_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("ASTRIX_API_TOKEN"))
    parser.add_argument("--flush", type=float, default=0.25, help="seconds between API posts")
    args = parser.parse_args(argv)

    if not args.emulate and not args.port:
        parser.error("give --port or --emulate")

    board = None
    serial_port = None
    if args.emulate:
        board = EmulatedBoard()
    else:
        try:
            import serial  # pyserial
        except ImportError:
            print("pyserial is required: pip install pyserial", file=sys.stderr)
            return 2
        serial_port = serial.Serial(args.port, args.baud, timeout=0.05)
        time.sleep(2.0)  # most boards reset when the port opens

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    url = f"{args.api.rstrip('/')}/hardware/telemetry"
    readings: list[dict] = []
    acks: list[str] = []
    last_flush = time.monotonic()
    print(f"bridging {'emulator' if board else args.port} -> {url}  (ctrl-c to stop)")
    with httpx.Client(timeout=5.0, headers=headers) as client:
        try:
            while True:
                if board is not None:
                    reading = board.read()
                    if reading:
                        readings.append(reading)
                    acks.extend(board.pending)
                    board.pending.clear()
                    time.sleep(0.1)
                else:
                    line = serial_port.readline().decode("utf-8", errors="replace").strip()
                    if line.startswith("{"):
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            data = None
                        if isinstance(data, dict):
                            (acks.append(line) if "ack" in data or "err" in data else readings.append(data))

                if time.monotonic() - last_flush >= args.flush and (readings or acks):
                    try:
                        body = {"readings": readings[-50:], "acks": acks[-20:], "transport": "serial-bridge"}
                        response = client.post(url, json=body)
                        response.raise_for_status()
                        for command in response.json().get("commands", []):
                            print(f"  -> {command}")
                            if board is not None:
                                board.write(command)
                            else:
                                serial_port.write((command + "\n").encode())
                    except httpx.HTTPError as exc:
                        print(f"  ! api error: {exc}")
                    readings, acks = [], []
                    last_flush = time.monotonic()
        except KeyboardInterrupt:
            print("\nstopped")
    if serial_port is not None:
        serial_port.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
