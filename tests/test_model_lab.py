"""Training corpus security and the nano model."""

from __future__ import annotations

import sqlite3

from backend.app.training import ModelLab, build_example


def cycle(i: int, subsystem: str, action: str, params: list[str]) -> dict:
    return {
        "cycle_id": f"c{i}",
        "spacecraft_id": "ASTRIX-01",
        "detection": {
            "severity": "WARNING",
            "ml_score": 0.5 + (i % 7) / 20,
            "final_score": 0.6,
            "persistence_seconds": i,
            "deviating_parameters": params,
            "context_factors": [{"name": "ml_score", "value": 0.5, "weight": 0.4}],
            "suppressed": False,
        },
        "diagnosis": {"subsystem": subsystem, "probable_cause": "x", "failure_mode": f"{subsystem}_fault", "confidence": 0.8, "reasoner": "DETERMINISTIC"},
        "risk": {"severity": "WARNING", "mission_impact": "MEDIUM", "reasoner": "DETERMINISTIC"},
        "plan": {"options": [{"action_id": action, "risk_level": "GREEN"}], "selected_action_id": action, "reasoner": "DETERMINISTIC"},
        "safety": {"status": "PASS"},
    }


def make_lab(tmp_path, every=0):
    return ModelLab(str(tmp_path / "corpus" / "c.db"), None, every)


def test_suppressed_or_undiagnosed_cycles_are_not_examples():
    c = cycle(1, "ADCS", "isolate_wheel_3", ["wheel_3_vibration"])
    c["detection"]["suppressed"] = True
    assert build_example(c) is None
    assert build_example({"detection": {}, "diagnosis": None}) is None


def test_payloads_are_encrypted_and_deduplicated(tmp_path):
    lab = make_lab(tmp_path)
    c = cycle(1, "ADCS", "isolate_wheel_3", ["wheel_3_vibration"])
    lab._on_cycle("cycle", c)
    lab._on_cycle("cycle", c)  # identical situation and decision
    assert lab.corpus.count() == 1
    raw = sqlite3.connect(lab.corpus.path).execute("SELECT payload FROM examples").fetchone()[0]
    assert b"wheel_3_vibration" not in raw
    assert lab.corpus.examples()[0]["situation"]["deviating_parameters"] == ["wheel_3_vibration"]


def test_hash_chain_detects_tampering(tmp_path):
    lab = make_lab(tmp_path)
    for i in range(4):
        lab._on_cycle("cycle", cycle(i, "EPS", "enter_power_save", ["state_of_charge"]))
    assert lab.corpus.verify()["ok"]
    conn = sqlite3.connect(lab.corpus.path)
    conn.execute("DELETE FROM examples WHERE id = 2")
    conn.commit()
    result = lab.corpus.verify()
    assert not result["ok"] and result["broken_at"] == 3


def test_nano_model_learns_the_mapping_and_exports_sft(tmp_path):
    lab = make_lab(tmp_path)
    for i in range(30):
        if i % 2:
            lab._on_cycle("cycle", cycle(i, "ADCS", "isolate_wheel_3", ["wheel_3_vibration", "attitude_error_deg"]))
        else:
            lab._on_cycle("cycle", cycle(i, "EPS", "enter_power_save", ["state_of_charge", "battery_voltage"]))
    result = lab.nano.train()
    assert result["trained"], result
    prediction = lab.nano.predict({"severity": "WARNING", "ml_score": 0.7, "final_score": 0.6, "deviating_parameters": ["wheel_3_vibration"]})
    assert prediction["subsystem"]["label"] == "ADCS"
    assert prediction["action_id"]["label"] == "isolate_wheel_3"

    records = lab.corpus.export_sft()
    assert len(records) == 30 and records[0]["messages"][0]["role"] == "system"

    # A reloaded lab picks up the versioned model after its integrity check.
    reloaded = make_lab(tmp_path)
    assert reloaded.nano.version == result["version"]


def test_too_few_examples_is_reported_not_raised(tmp_path):
    lab = make_lab(tmp_path)
    assert lab.nano.train()["trained"] is False
