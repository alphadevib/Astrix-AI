"""Mission summaries and open-world anomaly handling.

Two claims are under test. First, that a finished mission becomes one training
example carrying what only the whole mission shows — what recurred, what worked,
what was injected but never explained. Second, that an anomaly outside the
failure-mode catalogue is recognised, given a stable signature, answered from
precedent, and never granted authority it should not have.
"""

from __future__ import annotations

from backend.app.agents.knowledge import ACTION_CATALOG
from backend.app.training.mission_summary import (
    build_mission_summary,
    render_narrative,
    summary_to_example,
)
from backend.app.training.novelty import (
    CONSERVATIVE_ACTIONS,
    OpenWorldAdvisor,
    channel_family,
    describe,
    is_novel,
    signature,
)

MISSION = {
    "spacecraft_id": "ASTRIX-01",
    "phase": "ORBIT",
    "simulated_seconds": 3600,
    "active_scenario_title": "Reaction wheel degradation",
    "vehicle": {"name": "Astrix-1"},
}


def event(kind, **payload):
    return {"type": kind, "payload": payload}


def wheel_mission():
    return [
        event("mission_started", spacecraft_id="ASTRIX-01"),
        event("orbit_acquired"),
        event("fault_injected", scenario="wheel_degradation", title="Wheel degradation", is_fault=True, seq=100),
        event("detection", severity="WARNING"),
        event("diagnosis", subsystem="ADCS", failure_mode="wheel_degradation", confidence=0.8, reasoner="DETERMINISTIC"),
        event("executed", action_id="isolate_wheel_3", seq=140),
        event("outcome", action_id="isolate_wheel_3", outcome="SUCCESSFUL", effectiveness=0.9),
        event("mission_stopped"),
    ]


# ------------------------------------------------------------- summaries


def test_summary_captures_the_arc_of_a_mission():
    summary = build_mission_summary(wheel_mission(), MISSION)

    assert summary["mission"]["phase_reached"] == "ORBIT"
    assert summary["mission"]["milestones"] == ["orbit_acquired"]
    assert summary["observed"]["severity_counts"] == {"WARNING": 1}
    assert summary["observed"]["diagnoses"][0]["failure_mode"] == "wheel_degradation"
    assert summary["findings"]["action_effectiveness"] == {"isolate_wheel_3": 0.9}
    assert summary["findings"]["verdict"] == "RECOVERED"


def test_summary_flags_a_fault_that_was_injected_but_never_diagnosed():
    events = [
        event("mission_started"),
        event("fault_injected", scenario="gyro_drift", title="Gyro drift", is_fault=True),
        event("detection", severity="WARNING"),
        event("diagnosis", subsystem="ADCS", failure_mode="unclassified", reasoner="DETERMINISTIC"),
        event("mission_stopped"),
    ]
    findings = build_mission_summary(events, MISSION)["findings"]

    # The most useful training signal there is: a gap in the catalogue.
    assert findings["undiagnosed_injections"] == ["gyro_drift"]
    assert findings["unexplained_anomalies"] == 1
    assert findings["verdict"] == "UNEXPLAINED_BEHAVIOUR"


def test_summary_notices_a_recovery_that_did_not_work():
    events = wheel_mission()[:-1] + [
        event("executed", action_id="isolate_wheel_3", seq=200),
        event("outcome", action_id="isolate_wheel_3", outcome="FAILED", effectiveness=0.1),
        event("diagnosis", subsystem="ADCS", failure_mode="wheel_degradation", reasoner="DETERMINISTIC"),
    ]
    findings = build_mission_summary(events, MISSION)["findings"]

    assert "wheel_degradation" in findings["recurring_failure_modes"]
    assert "isolate_wheel_3" in findings["actions_repeated"]
    assert findings["verdict"] == "RECOVERY_INEFFECTIVE"


def test_a_quiet_mission_produces_no_example():
    events = [event("mission_started"), event("orbit_acquired"), event("mission_stopped")]
    summary = build_mission_summary(events, MISSION)

    assert summary_to_example(summary) is None, "a mission where nothing happened taught something"


def test_example_is_shaped_like_the_frame_examples():
    example = summary_to_example(build_mission_summary(wheel_mission(), MISSION))

    assert example["scope"] == "mission"
    assert set(example) >= {"situation", "decision", "labels"}
    assert example["labels"]["failure_mode"] == "wheel_degradation"
    assert example["labels"]["action_id"] == "isolate_wheel_3"


def test_narrative_reads_as_a_debrief():
    text = render_narrative(build_mission_summary(wheel_mission(), MISSION))

    assert "ASTRIX-01" in text
    assert "wheel_degradation" in text
    assert "RECOVERED" in text


# ------------------------------------------------------------ open world


def test_channel_families_generalise_across_units():
    assert channel_family("wheel_3_vibration") == "wheel_vibration"
    assert channel_family("wheel_1_vibration") == "wheel_vibration"
    assert channel_family("battery_voltage") == "battery_voltage"


def test_signature_is_stable_and_order_independent():
    a = signature(["wheel_3_vibration", "bus_voltage"], "WARNING")
    b = signature(["bus_voltage", "wheel_3_vibration"], "WARNING")
    c = signature(["wheel_1_vibration", "bus_voltage"], "WARNING")

    assert a == b == c  # same shape, different unit and ordering
    assert a.startswith("nov-")
    assert a != signature(["bus_voltage", "solar_array_current"], "WARNING")
    assert signature([], "WARNING") == ""


def test_only_a_real_unexplained_alarm_counts_as_novel():
    alarm = {"severity": "WARNING", "deviating_parameters": ["a_x", "b_y"]}

    assert is_novel({"failure_mode": None}, alarm)
    assert is_novel({"failure_mode": "unclassified"}, alarm)
    # A catalogued diagnosis is not novel...
    assert not is_novel({"failure_mode": "wheel_degradation"}, alarm)
    # ...nor is a quiet frame, a suppressed one, or a single twitching channel.
    assert not is_novel({}, {"severity": "NORMAL", "deviating_parameters": ["a_x", "b_y"]})
    assert not is_novel({}, {"severity": "WARNING", "suppressed": True, "deviating_parameters": ["a_x", "b_y"]})
    assert not is_novel({}, {"severity": "WARNING", "deviating_parameters": ["a_x"]})


class FakeCorpus:
    def __init__(self, examples):
        self._examples = examples

    def recent_examples(self, limit=200):
        return self._examples[:limit]


def test_precedent_drives_the_proposal():
    corpus = FakeCorpus(
        [
            {
                "situation": {"deviating_parameters": ["wheel_2_vibration", "wheel_2_current"]},
                "labels": {"failure_mode": "wheel_degradation", "action_id": "isolate_wheel_3"},
            }
        ]
    )
    record = describe({"severity": "WARNING", "deviating_parameters": ["wheel_3_vibration", "wheel_3_current"]})
    proposal = OpenWorldAdvisor(corpus).propose(record)

    assert proposal["action_id"] == "isolate_wheel_3"
    assert proposal["precedents"][0]["similarity"] == 1.0
    assert proposal["confidence"] > 0.3


def test_an_unrecognised_fault_falls_back_to_the_least_harmful_action():
    advisor = OpenWorldAdvisor(FakeCorpus([]))

    warning = advisor.propose(describe({"severity": "WARNING", "deviating_parameters": ["x_a", "y_b"]}))
    critical = advisor.propose(describe({"severity": "CRITICAL", "deviating_parameters": ["x_a", "y_b"]}))

    assert warning["action_id"] == "increase_monitoring_rate"
    assert critical["action_id"] == "enter_safe_mode"
    assert warning["confidence"] < 0.5


def test_a_proposal_never_invents_an_action_or_claims_authority():
    corpus = FakeCorpus(
        [
            {
                "situation": {"deviating_parameters": ["x_a", "y_b"]},
                "labels": {"failure_mode": "invented", "action_id": "vent_the_airlock"},
            }
        ]
    )
    record = describe({"severity": "WARNING", "deviating_parameters": ["x_a", "y_b"]})

    for advisor in (OpenWorldAdvisor(corpus), OpenWorldAdvisor(FakeCorpus([]))):
        proposal = advisor.propose(record)
        assert proposal["authority"].startswith("advisory")
        if proposal["action_id"] not in CONSERVATIVE_ACTIONS:
            # Whatever precedent suggests, it has to be an action the twin models
            # and the safety engine can rule on.
            assert proposal["action_id"] in ACTION_CATALOG


def test_conservative_actions_exist_in_the_action_catalogue():
    for action_id in CONSERVATIVE_ACTIONS:
        assert action_id in ACTION_CATALOG, f"{action_id} is not a modelled recovery action"


def test_advisor_survives_an_unreadable_corpus():
    class Broken:
        def recent_examples(self, limit=200):
            raise RuntimeError("corpus is locked")

    proposal = OpenWorldAdvisor(Broken()).propose(
        describe({"severity": "WARNING", "deviating_parameters": ["x_a", "y_b"]})
    )
    assert proposal["action_id"] == "increase_monitoring_rate"
