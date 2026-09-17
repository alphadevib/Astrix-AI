"""Regression tests for the loop-logic loopholes.

Each test pins one behaviour that was previously broken:

* a restarted telemetry session inherited the old session's anomaly state;
* an approval stayed live after its anomaly closed or the mission restarted;
* an escalation during outcome measurement planned and executed a second action,
  overwriting the first action's measurement;
* an approved action was scored against the spacecraft state from when the plan
  was made instead of when it was executed;
* a FAILED action was re-proposed immediately, in a loop.
"""

from __future__ import annotations

from backend.app.core.enums import ApprovalStatus, ExecutionStatus
from backend.app.services.pipeline import FAILED_ACTION_COOLDOWN_FRAMES
from telemetry.simulator import SpacecraftSimulator


def run_until(astrix, sim, predicate, max_frames=2500, apply_commands=True):
    """Step the simulator through the pipeline until `predicate(result)` holds."""
    for _ in range(max_frames):
        frame = sim.step()
        result = astrix.pipeline.ingest(frame)
        if apply_commands:
            for command in astrix.pipeline.pop_commands(frame.spacecraft_id):
                sim.apply_recovery(command["action_id"])
        if predicate(result):
            return result
    raise AssertionError("condition not reached")


def open_wheel_anomaly_awaiting_approval(astrix):
    sim = SpacecraftSimulator(seed=42)
    for _ in range(30):
        astrix.pipeline.ingest(sim.step())
    sim.inject("wheel_degradation")
    result = run_until(astrix, sim, lambda r: r.execution_status is ExecutionStatus.SIMULATED)
    return sim, result


def test_sequence_restart_starts_a_clean_session(astrix):
    sim, result = open_wheel_anomaly_awaiting_approval(astrix)
    pipeline = astrix.pipeline
    assert pipeline._active and pipeline.pending_approvals()

    fresh = SpacecraftSimulator(seed=1)
    pipeline.ingest(fresh.step())  # seq 1 < previous seq

    assert not pipeline._active
    assert not pipeline.pending_approvals()
    assert astrix.buffer.size(fresh.spacecraft_id) == 1


def test_approval_expires_when_mission_resets(astrix):
    _, result = open_wheel_anomaly_awaiting_approval(astrix)
    pending = astrix.pipeline.pending_approvals()[0]

    astrix.pipeline.reset("ASTRIX-01")
    outcome = astrix.pipeline.approve(
        pending["anomaly_id"], pending["action_id"], approved=True, operator="test"
    )

    assert outcome.approval is ApprovalStatus.REJECTED
    assert outcome.execution_status is ExecutionStatus.NOT_EXECUTED
    assert astrix.pipeline.pop_commands("ASTRIX-01") == []


def test_approval_expires_when_anomaly_closes(astrix):
    _, result = open_wheel_anomaly_awaiting_approval(astrix)
    pending = astrix.pipeline.pending_approvals()[0]

    astrix.pipeline._close_active("ASTRIX-01", "telemetry returned to nominal")
    outcome = astrix.pipeline.approve(
        pending["anomaly_id"], pending["action_id"], approved=True, operator="test"
    )

    assert outcome.execution_status is ExecutionStatus.NOT_EXECUTED
    assert not astrix.pipeline.pending_approvals()


def test_no_second_plan_while_a_recovery_is_being_measured(astrix):
    sim, _ = open_wheel_anomaly_awaiting_approval(astrix)
    pending = astrix.pipeline.pending_approvals()[0]
    decision = astrix.pipeline.approve(
        pending["anomaly_id"], pending["action_id"], approved=True, operator="test"
    )
    assert decision.approval is ApprovalStatus.APPROVED

    measuring = astrix.pipeline._pending["ASTRIX-01"]
    # Hold the command back so the fault keeps developing and severity can escalate.
    held = astrix.pipeline.pop_commands("ASTRIX-01")
    assert held

    planned_again = False
    for _ in range(astrix.settings.outcome_evaluation_frames - 1):
        result = astrix.pipeline.ingest(sim.step())
        if result.plan is not None:
            planned_again = True
    assert not planned_again
    assert astrix.pipeline._pending.get("ASTRIX-01") is measuring


def test_outcome_baseline_is_taken_at_execution_not_at_decision(astrix):
    sim, decision_result = open_wheel_anomaly_awaiting_approval(astrix)
    pending = astrix.pipeline.pending_approvals()[0]
    queued = astrix.pipeline._awaiting_approval[pending["anomaly_id"]]
    decided_at = queued.evaluate_at_seq

    # The operator takes a while; the fault keeps growing meanwhile.
    for _ in range(60):
        astrix.pipeline.ingest(sim.step())
    latest = astrix.buffer.latest("ASTRIX-01")

    astrix.pipeline.approve(pending["anomaly_id"], pending["action_id"], approved=True, operator="test")
    measuring = astrix.pipeline._pending["ASTRIX-01"]

    assert measuring.evaluate_at_seq == latest.seq + astrix.settings.outcome_evaluation_frames
    assert measuring.evaluate_at_seq > decided_at
    assert measuring.baseline_indicator == astrix.pipeline._indicator(latest, queued.diagnosis.subsystem)


def test_failed_action_is_not_immediately_repeated(astrix):
    sim, _ = open_wheel_anomaly_awaiting_approval(astrix)
    pending = astrix.pipeline.pending_approvals()[0]
    astrix.pipeline._failed_actions["ASTRIX-01"] = {pending["action_id"]: sim.seq}

    # Force a re-plan on the next escalation by reopening the anomaly.
    astrix.pipeline.reset("ASTRIX-01")
    astrix.pipeline._failed_actions["ASTRIX-01"] = {pending["action_id"]: sim.seq}
    astrix.pipeline._last_seq["ASTRIX-01"] = sim.seq

    proposals = []
    for _ in range(400):
        result = astrix.pipeline.ingest(sim.step())
        if result.safety is not None and result.safety.action_id:
            proposals.append(result.safety.action_id)
        if sim.seq - astrix.pipeline._failed_actions["ASTRIX-01"][pending["action_id"]] >= FAILED_ACTION_COOLDOWN_FRAMES:
            break
    assert pending["action_id"] not in proposals
