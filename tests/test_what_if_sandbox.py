"""The operator what-if sandbox calls the stage endpoints with only an action id."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from backend.app.agents.knowledge import ACTION_CATALOG
from backend.app.core.schemas import TelemetryFrame

SANDBOX_ACTIONS = [
    "isolate_wheel_3",
    "switch_redundant_wheel_config",
    "reduce_wheel_speed",
    "restart_wheel_3",
    "recalibrate_gyro_bias",
    "switch_to_redundant_sensor",
    "enter_power_save",
    "reduce_payload_duty_cycle",
    "switch_data_processing_mode",
    "alter_task_schedule",
    "reduce_downlink_rate",
    "enter_safe_mode",
]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("whatif")
    os.environ["ASTRIX_DATABASE_URL"] = f"sqlite:///{(tmp / 'api.db').as_posix()}"
    os.environ["ASTRIX_VECTOR_PATH"] = str(tmp / "vectors.json")
    from backend.app.config import get_settings

    get_settings.cache_clear()
    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
    for key in ("ASTRIX_DATABASE_URL", "ASTRIX_VECTOR_PATH"):
        os.environ.pop(key, None)


def test_sandbox_actions_are_all_in_the_catalogue():
    assert set(SANDBOX_ACTIONS) <= set(ACTION_CATALOG)


@pytest.mark.parametrize("action_id", SANDBOX_ACTIONS)
def test_simulate_with_action_id_only(client, action_id):
    frame = TelemetryFrame().model_dump(mode="json")
    response = client.post("/recovery/simulate", json={"action_id": action_id, "frame": frame})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action_id"] == action_id
    assert body["status"] in {"PASS", "FAIL"}
    assert body["trajectory"]["attitude_error_deg"]


def test_verify_with_action_id_only(client):
    frame = TelemetryFrame().model_dump(mode="json")
    response = client.post("/recovery/verify", json={"action_id": "enter_power_save", "frame": frame})
    assert response.status_code == 200, response.text


def test_unknown_or_missing_action_is_a_client_error(client):
    frame = TelemetryFrame().model_dump(mode="json")
    assert client.post("/recovery/simulate", json={"action_id": "nope", "frame": frame}).status_code == 422
    assert client.post("/recovery/simulate", json={"frame": frame}).status_code == 422
