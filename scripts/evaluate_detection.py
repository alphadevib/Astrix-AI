"""Measure ASTRIX detection quality: false alarms on nominal flight, and
time-to-detect for every injected fault.

This is the number a demo claim rests on. "ASTRIX detects faults" means nothing
unless it also stays quiet through several full orbits of healthy telemetry —
eclipse, payload passes, ground contacts and slews included.

    python scripts/evaluate_detection.py                 # 3 nominal orbits + every scenario
    python scripts/evaluate_detection.py --orbits 1 --scenarios wheel_degradation

Runs headless against a throwaway database, with LLM reasoning disabled so the
result is deterministic. Exit code is 0 only if nominal flight raised no
WARNING/CRITICAL anomaly and every real fault was detected.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.app.bootstrap import Astrix  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.core.enums import Severity  # noqa: E402
from telemetry.scenarios import BENIGN_SCENARIOS, SCENARIOS  # noqa: E402
from telemetry.simulator import ORBIT_PERIOD_S, SpacecraftSimulator  # noqa: E402

SETTLE_FRAMES = 30


def build(tmp: Path) -> Astrix:
    tmp.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite:///{(tmp / 'eval.db').as_posix()}",
        vector_path=str(tmp / "vectors.json"),
        llm_enabled=False,
    )
    return Astrix(settings)


def soak(astrix: Astrix, frames: int, seed: int) -> dict:
    """Healthy flight only. Anything at WARNING or above is a false alarm."""
    sim = SpacecraftSimulator(seed=seed)
    worst = Counter()
    alarms: list[str] = []
    in_alarm = False
    for _ in range(frames):
        frame = sim.step()
        result = astrix.pipeline.ingest(frame)
        _apply(astrix, sim, frame.spacecraft_id)
        d = result.detection
        level = "SUPPRESSED" if d.suppressed else d.severity.value
        worst[level] += 1
        alarming = d.is_anomaly and not d.suppressed and d.severity.rank >= Severity.WARNING.rank
        if alarming and not in_alarm:
            phase = (sim.t % ORBIT_PERIOD_S) / ORBIT_PERIOD_S
            alarms.append(
                f"seq {frame.seq} phase {phase:.3f} mode {frame.mode.value} "
                f"{d.severity.value} score {d.final_score:.2f} ml {d.ml_score:.2f} "
                f"channels {', '.join(d.deviating_parameters[:5])}"
            )
        in_alarm = alarming
    return {"levels": dict(worst), "alarms": alarms}


def detect(astrix: Astrix, key: str, seed: int, max_frames: int) -> dict:
    """Seconds from injection to the first WARNING-or-above, unsuppressed detection."""
    sim = SpacecraftSimulator(seed=seed)
    for _ in range(SETTLE_FRAMES):
        frame = sim.step()
        astrix.pipeline.ingest(frame)
    sim.inject(key)
    first_watch = first_alarm = None
    diagnosis = action = None
    for n in range(1, max_frames + 1):
        frame = sim.step()
        result = astrix.pipeline.ingest(frame)
        d = result.detection
        if first_watch is None and d.is_anomaly and not d.suppressed:
            first_watch = n
        if first_alarm is None and d.is_anomaly and not d.suppressed and d.severity.rank >= Severity.WARNING.rank:
            first_alarm = n
        if result.diagnosis and diagnosis is None and first_alarm is not None:
            diagnosis = f"{result.diagnosis.subsystem.value}: {result.diagnosis.probable_cause[:70]}"
        if result.safety and result.safety.action_id and action is None:
            action = f"{result.safety.action_id} ({result.safety.approval.value})"
        if first_alarm is not None and action is not None:
            break
    return {"watch_s": first_watch, "alarm_s": first_alarm, "diagnosis": diagnosis, "action": action}


def _apply(astrix: Astrix, sim: SpacecraftSimulator, spacecraft_id: str) -> None:
    for command in astrix.pipeline.pop_commands(spacecraft_id):
        sim.apply_recovery(command["action_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate ASTRIX detection quality")
    parser.add_argument("--orbits", type=float, default=3.0, help="nominal orbits to soak")
    parser.add_argument("--scenarios", nargs="*", default=sorted(SCENARIOS))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-frames", type=int, default=1500)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        frames = int(args.orbits * ORBIT_PERIOD_S)
        if frames > 0:
            astrix = build(Path(tmp) / "soak")
            print(f"nominal soak: {frames} frames ({args.orbits:g} orbits)")
            report = soak(astrix, frames, args.seed)
            print(f"  frames by level: {report['levels']}")
            if report["alarms"]:
                failed = True
                print(f"  FALSE ALARMS ({len(report['alarms'])}):")
                for line in report["alarms"][:20]:
                    print(f"    {line}")
            else:
                print("  no false alarms")
            astrix.engine.dispose()

        for key in args.scenarios:
            workdir = Path(tmp) / key
            workdir.mkdir(parents=True, exist_ok=True)
            astrix = build(workdir)
            outcome = detect(astrix, key, args.seed, args.max_frames)
            benign = key in BENIGN_SCENARIOS
            if benign:
                ok = outcome["alarm_s"] is None
                verdict = "OK (stayed quiet)" if ok else f"FALSE ALARM at +{outcome['alarm_s']}s"
            else:
                ok = outcome["alarm_s"] is not None
                verdict = f"detected +{outcome['alarm_s']}s" if ok else "MISSED"
            failed |= not ok
            print(f"\n{key:26s} {verdict}   first WATCH +{outcome['watch_s']}s")
            if outcome["diagnosis"]:
                print(f"  diagnosis  {outcome['diagnosis']}")
            if outcome["action"]:
                print(f"  action     {outcome['action']}")
            astrix.engine.dispose()

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
