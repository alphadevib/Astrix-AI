"""Run one complete ASTRIX scenario headless and print every stage.

This is both the demo rehearsal tool and the project's end-to-end smoke test: it
exercises detection, memory recall, diagnosis, risk, planning, safety
verification, the digital twin, execution, outcome measurement and mission
learning in one process, with no server and no browser.

    python scripts/run_scenario.py                                # wheel degradation
    python scripts/run_scenario.py --scenario benign_thermal_transient
    python scripts/run_scenario.py --no-llm                       # deterministic reasoners
    python scripts/run_scenario.py --list

Exit code is 0 only if the loop reached a decision, so CI can run it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The Windows console defaults to cp1252, which cannot encode the degree signs,
# arrows and dashes this report prints.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from backend.app.bootstrap import Astrix  # noqa: E402
from backend.app.config import get_settings  # noqa: E402
from backend.app.core.enums import ApprovalStatus, VerificationStatus  # noqa: E402
from backend.app.core.schemas import LoopResult  # noqa: E402
from telemetry.scenarios import SCENARIOS  # noqa: E402
from telemetry.simulator import SpacecraftSimulator  # noqa: E402

RULE = "=" * 78
THIN = "-" * 78


def h1(text: str) -> None:
    print(f"\n{RULE}\n  {text}\n{RULE}")


def h2(text: str) -> None:
    print(f"\n{THIN}\n{text}\n{THIN}")


def show_detection(result: LoopResult) -> None:
    d = result.detection
    flag = "SUPPRESSED" if d.suppressed else d.severity.value
    print(f"  severity            {flag}")
    print(f"  ML anomaly score    {d.ml_score:.3f}")
    print(f"  context-adj. score  {d.final_score:.3f}")
    print(f"  elevated for        {d.persistence_seconds:.0f}s")
    print(f"  channels >3 sigma   {', '.join(d.deviating_parameters[:6]) or 'none'}")
    print("\n  score breakdown:")
    for factor in d.context_factors:
        bar = "#" * int(round(factor.contribution * 60))
        print(f"    {factor.name:<24} {factor.contribution:+.3f} {bar}")
        print(f"      {factor.note}")
    if d.suppression_reason:
        print(f"\n  SUPPRESSION: {d.suppression_reason}")


def show_recall(result: LoopResult) -> None:
    if result.recall is None or not result.recall.hits:
        print("  no comparable historical record retrieved")
        return
    for hit in result.recall.hits:
        print(f"  [{hit.similarity:.2f}] {hit.title}")
        print(
            f"         mission={hit.mission_id}  failure_mode={hit.failure_mode or 'n/a'}  "
            f"recovery={hit.recovery_action or 'n/a'}  outcome={hit.outcome.value}"
        )
    if result.recall.lessons:
        print("\n  lessons on record:")
        for lesson in result.recall.lessons:
            print(f"    - {lesson}")
    if result.recall.action_success_rates:
        print("\n  measured recovery effectiveness:")
        for action_id, rate in sorted(
            result.recall.action_success_rates.items(), key=lambda kv: -kv[1]
        ):
            print(f"    {action_id:<34} {rate:.2f}")


def show_diagnosis(result: LoopResult) -> None:
    d = result.diagnosis
    if d is None:
        print("  (not reached)")
        return
    print(f"  subsystem           {d.subsystem.value}")
    print(f"  component           {d.component or 'unspecified'}")
    print(f"  failure mode        {d.failure_mode or 'unclassified'}")
    print(f"  confidence          {d.confidence:.2f}")
    print(f"  reasoner            {d.reasoner.value}")
    print(f"\n  {d.probable_cause}")
    if d.evidence:
        print("\n  evidence:")
        for item in d.evidence:
            print(f"    - {item}")
    if d.ruled_out:
        print("\n  ruled out:")
        for item in d.ruled_out:
            print(f"    - {item}")


def show_risk(result: LoopResult) -> None:
    r = result.risk
    if r is None:
        print("  (not reached)")
        return
    print(f"  mission impact      {r.mission_impact}")
    print(
        f"  domains             power={r.power_risk}  attitude={r.attitude_risk}  "
        f"thermal={r.thermal_risk}  comms={r.communication_risk}"
    )
    if r.time_to_impact_minutes is not None:
        print(f"  time to impact      {r.time_to_impact_minutes:.0f} min")
    for cascade in r.cascading_risks:
        print(f"  cascade             {cascade}")
    print(f"\n  {r.rationale}")


def show_plan(result: LoopResult) -> None:
    plan = result.plan
    if plan is None:
        print("  (not reached)")
        return
    print(f"  reasoner            {plan.reasoner.value}")
    print(f"  selected            {plan.selected_action_id}\n")
    print(f"  {'#':<3}{'action':<34}{'risk':<8}{'hist':<7}{'score':<7}")
    for index, option in enumerate(plan.options, start=1):
        marker = ">" if option.action_id == plan.selected_action_id else " "
        hist = f"{option.historical_success_rate:.2f}" if option.historical_success_rate is not None else "  - "
        print(
            f"{marker} {index:<3}{option.action_id:<34}{option.risk_level.value:<8}"
            f"{hist:<7}{option.score:<7.3f}"
        )
        print(f"      {option.rationale}")


def show_simulation(result: LoopResult) -> None:
    sim = result.simulation
    if sim is None:
        print("  (not reached)")
        return
    print(f"  action              {sim.action_id}")
    print(f"  verdict             {sim.status.value}")
    print(f"  horizon             {sim.horizon_seconds}s")
    print(f"  predicted benefit   {sim.effectiveness_estimate:.0%} vs doing nothing\n")
    for check in sim.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name:<32} {check.detail}")
    print(f"\n  {sim.summary}")


def show_safety(result: LoopResult) -> None:
    verdict = result.safety
    if verdict is None:
        print("  (not reached)")
        return
    print(f"  action              {verdict.action_id}")
    print(f"  verdict             {verdict.status.value}")
    print(f"  effective risk      {verdict.effective_risk_level.value}")
    print(f"  approval            {verdict.approval.value}")
    print(f"  audit id            {verdict.audit_id}\n")
    for rule in verdict.rules:
        mark = "VIOLATED" if rule.violated else "ok      "
        detail = f"  <- {rule.detail}" if rule.detail else ""
        print(f"  [{mark}] {rule.rule_id}  {rule.description}{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one ASTRIX scenario end to end")
    parser.add_argument("--scenario", default="wheel_degradation", help="fault to inject")
    parser.add_argument("--onset", type=int, default=40, help="frame at which to inject")
    parser.add_argument("--max-frames", type=int, default=900)
    parser.add_argument("--no-llm", action="store_true", help="force deterministic reasoners")
    parser.add_argument("--auto-approve", action="store_true", help="approve escalated actions")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args(argv)

    if args.list:
        for key, scenario in sorted(SCENARIOS.items()):
            print(f"{key:28s} [{scenario.subsystem:6s}] {scenario.description}")
        return 0
    if args.scenario not in SCENARIOS:
        print(f"unknown scenario '{args.scenario}'. Available: {', '.join(sorted(SCENARIOS))}")
        return 2

    settings = get_settings()
    if args.no_llm:
        settings.llm_enabled = False

    h1("ASTRIX — Autonomous Spacecraft Intelligence & eXecution")
    print("  Detect. Reason. Recover. Learn.")
    astrix = Astrix(settings)
    pipeline = astrix.pipeline
    scenario = SCENARIOS[args.scenario]

    print(f"\n  reasoner        {'LLM ' + settings.llm_model if astrix.llm.available else 'DETERMINISTIC'}")
    print(f"  autonomy limit  {settings.auto_execute_max_risk.value}")
    print(f"  memory          {astrix.memory.stats()['vector_documents']} documents, "
          f"{astrix.memory.stats()['recorded_outcomes']} recorded outcomes")

    sim = SpacecraftSimulator(dt=1.0, seed=42)
    decided: LoopResult | None = None
    injected = False
    approved_once = False

    h2("STEP 1 — healthy spacecraft")
    for _ in range(args.onset):
        result = pipeline.ingest(sim.step())
    frame = astrix.buffer.latest("ASTRIX-01")
    print(
        f"  mode={frame.mode.value}  soc={frame.state_of_charge:.1f}%  "
        f"temp={frame.temperature:.1f}C  pointing={frame.attitude_error_deg:.3f}deg  "
        f"worst wheel vibration={frame.wheel_vibration:.2f} mm/s"
    )
    print(f"  detection: {result.detection.severity.value} "
          f"(ml={result.detection.ml_score:.3f}, final={result.detection.final_score:.3f})")

    h2(f"STEP 2 — inject: {scenario.title}")
    print(f"  {scenario.description}")
    print(f"  ramps over {scenario.ramp_seconds:.0f}s; expected signals: "
          f"{', '.join(scenario.expected_signals)}")
    sim.inject(args.scenario)
    injected = True

    for _ in range(args.max_frames):
        frame = sim.step()
        result = pipeline.ingest(frame)

        # Apply any command ASTRIX issued, closing the loop on the spacecraft.
        for command in pipeline.pop_commands(frame.spacecraft_id):
            effect = sim.apply_recovery(command["action_id"])
            print(f"\n  >> COMMAND APPLIED: {command['action_id']} -> {effect}")

        # Capture the cycle that actually planned a recovery — a WATCH-severity
        # cycle produces a diagnosis but stops before planning by design.
        if decided is None and result.plan is not None:
            decided = result
            h2(f"STEP 3 — detection (frame {frame.seq})")
            show_detection(result)
            h2("STEP 4 — mission memory recall")
            show_recall(result)
            h2("STEP 5 — diagnosis")
            show_diagnosis(result)
            h2("STEP 6 — risk assessment")
            show_risk(result)
            h2("STEP 7 — recovery planning")
            show_plan(result)
            h2("STEP 8 — digital twin simulation")
            show_simulation(result)
            h2("STEP 9 — deterministic safety verification")
            show_safety(result)

            verdict = result.safety
            if verdict and verdict.approval is ApprovalStatus.PENDING_APPROVAL and args.auto_approve:
                h2("STEP 9b — operator approval")
                approval = pipeline.approve(
                    anomaly_id=result.anomaly_id,
                    action_id=verdict.action_id,
                    approved=True,
                    operator="demo-flight-director",
                )
                print(f"  {approval.approval.value}: {approval.message}")
                approved_once = True
            elif verdict and verdict.approval is ApprovalStatus.AUTO_APPROVED:
                print(f"\n  AUTO-EXECUTED within the {settings.auto_execute_max_risk.value} "
                      f"autonomy limit")

        # Stop once the recovery has been executed, measured and learned from.
        if (
            decided is not None
            and not pipeline.has_pending_outcome(frame.spacecraft_id)
            and result.detection.severity.value == "NORMAL"
        ):
            break

    if decided is None:
        h2("RESULT")
        if injected:
            print("  the loop never reached a diagnosis — detection did not escalate this scenario")
            if scenario.key == "benign_thermal_transient":
                print("  for the benign scenario that is the CORRECT outcome")
                return 0
        return 1

    h2("STEP 10 — mission learning")
    stats = astrix.memory.stats()
    print(f"  memory now holds {stats['lessons']} lessons, {stats['recorded_outcomes']} "
          f"recorded outcomes, {stats['vector_documents']} documents")
    for lesson in astrix.memory.relevant_lessons(
        decided.diagnosis.subsystem if decided.diagnosis else None, limit=6
    ):
        print(f"    - {lesson}")

    h2("SPACECRAFT KNOWLEDGE PROFILE")
    profile = astrix.memory.profile("ASTRIX-01")
    print(f"  status              {profile.status}")
    print(f"  missions flown      {profile.missions_flown}")
    print(f"  anomaly counts      {profile.anomaly_counts}")
    print("  known limitations:")
    for item in profile.known_limitations:
        print(f"    - {item}")
    print("  latent risks:")
    for item in profile.latent_risks:
        print(f"    - {item}")

    h2("LOOP LATENCY")
    for timing in decided.timings:
        print(f"  {timing.stage:<16} {timing.milliseconds:>8.2f} ms")
    print(f"  {'TOTAL':<16} {decided.total_milliseconds:>8.2f} ms")

    h1("Today's anomaly becomes tomorrow's knowledge.")
    if approved_once:
        print("  recovery approved, executed, measured and recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
