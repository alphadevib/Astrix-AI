"""Reasoner registry, vehicle studio and the assistant/API surface."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in

from backend.app.agents.gateway import ModelGateway, _clean_json_text
from backend.app.agents.providers import PROVIDER_MAP, PROVIDERS
from backend.app.config import Settings


def test_gateway_catalogue_and_deterministic_fallback(monkeypatch):
    for spec in PROVIDERS:
        for key in spec.env_keys:
            monkeypatch.delenv(key, raising=False)
    # _env_file=None keeps a developer's real .env keys out of the test.
    gw = ModelGateway(Settings(_env_file=None, llm_enabled=False))
    assert not gw.available
    # The registry carries only providers whose credentials answer a live call.
    assert set(PROVIDER_MAP) == {"gemini", "groq", "huggingface", "local"}
    # The picker lists only providers that are usable: with no keys and no
    # Ollama, nothing is offered.
    assert gw.catalogue() == []
    # A stale ASTRIX_LLM_PROVIDER naming a removed provider must not pin the
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
