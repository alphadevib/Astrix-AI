"""Smoke-test a local Ollama model as ASTRIX's diagnostic reasoner.

Runs two copies of the simulator from the same seed — one clean, one with a
fault injected — so every telemetry delta is attributable to the fault alone.
The largest deltas go to the local model, which must answer in a fixed JSON
schema and pick its recovery action from the actions the simulator actually
models. Nothing the model says is executed.

    python scripts/ollama_diagnose.py                          # wheel degradation
    python scripts/ollama_diagnose.py --scenario benign_thermal_transient
    python scripts/ollama_diagnose.py --all                    # every scenario
    python scripts/ollama_diagnose.py --model qwen2.5-coder:7b

Exit code is 0 only if every run returned a schema-valid diagnosis.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from telemetry.scenarios import SCENARIOS  # noqa: E402
from telemetry.simulator import SpacecraftSimulator  # noqa: E402

# Mirrors SpacecraftSimulator.apply_recovery — the model may only choose these.
ALLOWED_ACTIONS = (
    "no_action",
    "increase_monitoring_rate",
    "isolate_wheel_3",
    "switch_redundant_wheel_config",
    "reduce_wheel_speed",
    "restart_wheel_3",
    "enter_power_save",
    "reduce_payload_duty_cycle",
    "enter_safe_mode",
    "switch_to_redundant_sensor",
    "recalibrate_gyro_bias",
    "reduce_downlink_rate",
    "prioritize_telemetry",
)

SKIP_FIELDS = {"seq", "sun_angle_deg", "position_km", "velocity_kms"}

SYSTEM = f"""You are the diagnostic reasoner for a LEO Earth-observation satellite.
You receive telemetry channels that deviate from a fault-free reference run at the
same orbital time; "sigma" is the shift in units of normal sensor noise. Decide whether this is a genuine fault, identify the subsystem
and most likely physical cause, and choose exactly one recovery action.

Rules:
- A deviation explained by environment (sun angle, eclipse) with no corroborating
  channel is NOT a fault: set is_fault=false and recommended_action="no_action".
- Channels are sorted by sigma. Find the root cause among the largest shifts;
  smaller shifts in other subsystems (e.g. battery current rising because a
  heater or CPU draws more power) are consequences, not the cause.
- If the primary and secondary temperature sensors disagree, suspect the sensor.
  If both rise together, the heat is real.
- Pick the action that removes the cause (e.g. a sensor bias is fixed by
  recalibration or switching sensors, not by reducing load).
- Prefer the least disruptive action that addresses the cause.
- recommended_action must be one of: {", ".join(ALLOWED_ACTIONS)}.
Respond with JSON only."""


class Diagnosis(BaseModel):
    is_fault: bool
    subsystem: Literal["ADCS", "POWER", "THERMAL", "COMMS", "CDH", "NONE"]
    probable_cause: str = Field(max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: Literal[ALLOWED_ACTIONS]  # type: ignore[valid-type]
    rationale: str = Field(max_length=500)


def _numeric_channels(frame) -> dict[str, float]:
    data = frame.model_dump()
    return {
        k: float(v)
        for k, v in data.items()
        if k not in SKIP_FIELDS and isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def build_observation(scenario_key: str, frames: int, window: int, seed: int) -> dict:
    """Run clean and faulted simulators in lockstep; summarise the fault's deltas."""
    clean = SpacecraftSimulator(seed=seed)
    faulty = SpacecraftSimulator(seed=seed)
    faulty.inject(scenario_key)

    hist_clean: dict[str, list[float]] = {}
    hist_fault: dict[str, list[float]] = {}
    last = None
    for i in range(frames):
        c, f = clean.step(), faulty.step()
        if i >= frames - window:
            for k, v in _numeric_channels(c).items():
                hist_clean.setdefault(k, []).append(v)
            for k, v in _numeric_channels(f).items():
                hist_fault.setdefault(k, []).append(v)
            last = f

    # Rank by shift in units of the clean run's own noise, not percent change:
    # percent change explodes for channels whose nominal value sits near zero
    # (battery current, gyro rates) and buries the channels that actually moved.
    deviations = []
    for k, observed in hist_fault.items():
        reference = hist_clean[k]
        ref = sum(reference) / window
        cur = sum(observed) / window
        std = (sum((v - ref) ** 2 for v in reference) / window) ** 0.5
        sigma = abs(cur - ref) / max(std, 0.02 * max(abs(ref), 1.0), 1e-3)
        deviations.append((sigma, k, ref, cur))
    deviations.sort(reverse=True)

    return {
        "mode": last.mode.value,
        "in_eclipse": last.in_eclipse,
        "sun_angle_deg": last.sun_angle_deg,
        "payload_active": last.payload_active,
        "window_seconds": window,
        "deviating_channels": [
            {"channel": k, "reference": round(ref, 4), "observed": round(cur, 4),
             "sigma": round(sigma, 1)}
            for sigma, k, ref, cur in deviations[:8]
            if sigma >= 3.0  # below this it is sensor noise, which only distracts the model
        ],
    }


def diagnose(client: httpx.Client, host: str, model: str, observation: dict) -> tuple[Diagnosis, float]:
    body = {
        "model": model,
        "stream": False,
        "think": False,  # qwen3 reasons out loud by default; too slow for a live loop
        "format": Diagnosis.model_json_schema(),
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(observation, indent=2)},
        ],
    }
    started = time.perf_counter()
    response = client.post(f"{host}/api/chat", json=body)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return Diagnosis.model_validate_json(content), elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test a local Ollama model on ASTRIX faults")
    parser.add_argument("--scenario", default="wheel_degradation", choices=sorted(SCENARIOS))
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--frames", type=int, default=900, help="simulated seconds after onset")
    parser.add_argument("--window", type=int, default=60, help="seconds averaged for comparison")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--show-input", action="store_true", help="print what the model sees")
    args = parser.parse_args(argv)

    keys = sorted(SCENARIOS) if args.all else [args.scenario]
    failures = 0
    with httpx.Client(timeout=180.0) as client:
        try:
            client.get(f"{args.host}/api/version").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Ollama not reachable at {args.host}: {exc}\nStart it with `ollama serve`.")
            return 2

        for key in keys:
            scenario = SCENARIOS[key]
            print(f"\n=== {key}  [{scenario.subsystem}]  expected signals: "
                  f"{', '.join(scenario.expected_signals) or '-'}")
            observation = build_observation(key, args.frames, args.window, args.seed)
            if args.show_input:
                print(json.dumps(observation, indent=2))
            try:
                result, seconds = diagnose(client, args.host, args.model, observation)
            except (httpx.HTTPError, ValidationError, KeyError, json.JSONDecodeError) as exc:
                failures += 1
                print(f"  FAILED: {exc.__class__.__name__}: {exc}")
                continue
            print(f"  fault      {result.is_fault}   subsystem {result.subsystem}   "
                  f"confidence {result.confidence:.2f}   ({seconds:.1f}s)")
            print(f"  cause      {result.probable_cause}")
            print(f"  action     {result.recommended_action}")
            print(f"  rationale  {result.rationale}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
