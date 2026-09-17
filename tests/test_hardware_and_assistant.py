"""Hardware-in-the-loop hub, reasoner registry and the assistant/API surface."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in

from backend.app.agents.gateway import ModelGateway, _clean_json_text
from backend.app.agents.providers import PROVIDERS, RETIRED
from backend.app.config import Settings
from backend.app.hardware import HardwareHub, HardwareReading, action_to_command
from backend.app.hardware.bridge import EmulatedBoard
from backend.app.hardware.hub import validate_command
from telemetry.simulator import SpacecraftSimulator


def reading(**kw) -> HardwareReading:
    base = dict(temp_c=24.0, bus_v=5.0, current_a=0.4, light=0.7, vib_g=0.1, gyro=[0, 0, 0], rpm=3000.0)
    base.update(kw)
    return HardwareReading(**base)


def test_command_validation():
    assert validate_command("inject vib_spike 0.8") == "INJECT VIB_SPIKE 0.8"
    assert validate_command("ACT WHEEL_SPEED 62") == "ACT WHEEL_SPEED 62"
    for bad in ("INJECT NOPE 0.5", "RATE 99", "rm -rf /", "ACT SELF_DESTRUCT"):
        with pytest.raises(ValueError):
            validate_command(bad)
    assert action_to_command("isolate_wheel_3") == "ACT ISOLATE_WHEEL"


def test_overlay_applies_deviation_from_calibrated_baseline():
    hub = HardwareHub()
    frame = SpacecraftSimulator(seed=1).step()
    hub.ingest([reading() for _ in range(HardwareHub.CALIBRATION_SAMPLES)])
    assert hub.baseline is not None
    assert hub.overlay(frame).temperature == pytest.approx(frame.temperature, abs=1e-6)

    hub.ingest([reading(temp_c=34.0, vib_g=0.9, fault="TEMP_BIAS", sev=0.8)])
    blended = hub.overlay(frame)
    assert blended.temperature == pytest.approx(frame.temperature + 10.0, abs=0.01)
    assert blended.wheel_3_vibration > frame.wheel_3_vibration + 1.5
    assert blended.injected_fault == "HIL: temp bias"


def test_queued_commands_are_returned_once():
    hub = HardwareHub()
    hub.queue("INJECT BROWNOUT 0.5")
    assert hub.ingest([reading()])["commands"] == ["INJECT BROWNOUT 0.5"]
    assert hub.ingest([reading()])["commands"] == []


def test_emulated_board_injects_and_recovers():
    board = EmulatedBoard()
    board.write("INJECT VIB_SPIKE 1.0")
    board.fault_t -= 30  # fully ramped
    assert board.read()["vib_g"] > 1.0
    board.write("ACT ISOLATE_WHEEL")
    r = board.read()
    assert r["fault"] == "NONE" and r["rpm"] < 100


def test_gateway_catalogue_and_deterministic_fallback(monkeypatch):
    for spec in PROVIDERS:
        for key in spec.env_keys:
            monkeypatch.delenv(key, raising=False)
    # _env_file=None keeps a developer's real .env keys out of the test.
    gw = ModelGateway(Settings(_env_file=None, llm_enabled=False))
    assert not gw.available
    keys = {p["key"] for p in gw.catalogue()}
    # The registry carries only providers whose credentials answer a live call,
    # plus Astrix's own on-board model, which needs no credential.
    assert keys == {"gemini", "groq", "huggingface", "local", "small_llm"}
    assert keys.isdisjoint(RETIRED), "a retired provider is back in the picker"
    # A stale ASTRIX_LLM_PROVIDER naming a retired provider must not pin the
    # console to a reasoner that no longer exists.
    assert ModelGateway(Settings(_env_file=None, llm_provider="anthropic")).active_provider != "anthropic"
    with pytest.raises(ValueError):
        ModelGateway(Settings(_env_file=None, llm_enabled=True, llm_provider="deterministic")).select("groq")
    assert _clean_json_text("<think>hmm</think>```json\n{\"a\": 1}\n```") == '{"a": 1}'


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


def test_notice_header_and_meta(client):
    response = client.get("/meta")
    assert "hypothetical" in response.json()["disclaimer"].lower()
    assert response.headers["x-astrix-notice"].startswith("Hypothetical")


def test_vehicle_endpoints(client):
    presets = client.get("/vehicles/presets").json()["presets"]
    report = client.post("/vehicles/analyse", json=presets[0]["design"]).json()
    assert report["analysis"]["feasible"] and report["preview"]["track"]
    generated = client.post("/vehicles/generate", json={"prompt": "cubesat to 500 km", "use_llm": False}).json()
    assert generated["design"]["satellite"]["bus"] == "cubesat"


def test_hardware_endpoints_round_trip(client):
    assert client.post("/hardware/command", json={"command": "INJECT DROPOUT 0.3"}).status_code == 200
    assert client.post("/hardware/command", json={"command": "format c:"}).status_code == 422
    body = {"readings": [reading().model_dump()], "acks": []}
    assert client.post("/hardware/telemetry", json=body).json()["commands"] == ["INJECT DROPOUT 0.3"]
    assert client.get("/hardware/status").json()["connected"]


def test_assistant_executes_commands(client):
    chat = lambda m: client.post("/assistant/chat", json={"message": m}).json()  # noqa: E731
    assert "hypothetical" in chat("help")["disclaimer"].lower()
    started = chat("start in orbit")
    assert "orbit" in started["reply"].lower()
    design = chat("design a 2-stage rocket for a 300 kg imaging satellite to 600 km")
    assert design["cards"][0]["kind"] == "design"
    intercept = chat("run an intercept check")
    assert "Pre-flight check" in intercept["reply"]
    stopped = chat("stop mission")
    assert stopped["reply"] == "Mission stopped."


def test_mission_flies_a_custom_vehicle(client):
    from telemetry.vehicles import PRESETS

    design = PRESETS["heavy-comsat"].model_dump()
    started = client.post("/mission/start", json={"vehicle": design, "launch_time_scale": 200}).json()
    assert started["vehicle"]["custom"] and started["vehicle"]["stages"] == 3
    bad = client.post("/mission/start", json={"vehicle": {"rocket": {"stages": []}}})
    assert bad.status_code == 422
