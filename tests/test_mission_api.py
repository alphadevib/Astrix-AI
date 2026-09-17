"""Mission runner phases and control-API guards, exercised through the real app."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in


@pytest.fixture
def client(tmp_path):
    os.environ["ASTRIX_DATABASE_URL"] = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    os.environ["ASTRIX_VECTOR_PATH"] = str(tmp_path / "vectors.json")
    os.environ["ASTRIX_CORPUS_PATH"] = str(tmp_path / "corpus" / "corpus.db")
    from backend.app.config import get_settings

    get_settings.cache_clear()
    from backend.app.main import app

    with TestClient(app) as test_client:
        sign_in(test_client)
        yield test_client
        test_client.post("/mission/stop")
    get_settings.cache_clear()
    for key in ("ASTRIX_DATABASE_URL", "ASTRIX_VECTOR_PATH", "ASTRIX_CORPUS_PATH"):
        os.environ.pop(key, None)


def wait_for_phase(client, phase, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/mission/status").json()
        if status["phase"] == phase:
            return status
        if status["phase"] == "FAILED":
            raise AssertionError(f"runner failed: {status['error']}")
        time.sleep(0.1)
    raise AssertionError(f"phase {phase} not reached; last status {status}")


def test_launch_flies_to_orbit_and_unlocks_injection(client):
    started = client.post(
        "/mission/start", json={"include_launch": True, "launch_time_scale": 200, "interval": 0.02, "settle_frames": 3}
    )
    assert started.status_code == 200

    # No satellite exists during ascent, so there is nothing to inject a fault into.
    early = client.post("/mission/inject/wheel_degradation")
    assert early.status_code == 409

    status = wait_for_phase(client, "ORBIT")
    keys = [m["key"] for m in status["milestones"]]
    assert keys[0] == "countdown" and keys[-1] == "detumble_complete"

    injected = client.post("/mission/inject/wheel_degradation")
    assert injected.status_code == 200
    assert client.get("/mission/status").json()["active_scenario"] == "wheel_degradation"


def test_injection_refused_when_mission_stopped(client):
    client.post("/mission/start", json={"include_launch": False, "interval": 0.02, "settle_frames": 2})
    wait_for_phase(client, "ORBIT")
    client.post("/mission/stop")
    assert client.post("/mission/inject/gyro_drift").status_code == 409


def test_unknown_scenario_is_rejected_before_restart(client):
    response = client.post("/mission/start", json={"scenario": "not_a_fault", "include_launch": False})
    assert response.status_code == 422
    assert client.get("/mission/status").json()["running"] is False


def test_external_telemetry_refused_while_simulating_same_spacecraft(client):
    client.post("/mission/start", json={"include_launch": False, "interval": 0.02, "settle_frames": 2})
    wait_for_phase(client, "ORBIT")
    frame = client.get("/telemetry/latest").json()["frame"]
    assert client.post("/telemetry", json=frame).status_code == 409

    frame["spacecraft_id"] = "OTHER-SAT"
    assert client.post("/telemetry", json=frame).status_code == 200


def test_launch_timeline_endpoint(client):
    body = client.get("/launch/timeline").json()
    assert body["target_altitude_km"] == 500.0
    assert body["milestones"][1]["key"] == "liftoff"
