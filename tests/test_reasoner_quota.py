"""The gateway warns before a provider's allowance runs out and skips it once it has."""

from __future__ import annotations

import httpx

from backend.app.agents.gateway import ModelGateway, _parse_duration

from conftest import make_settings


def _gateway(tmp_path, monkeypatch) -> ModelGateway:
    # Only Groq is configured, whatever keys the developer's environment holds.
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HF_TOKEN", "HUGGINGFACE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    settings = make_settings(tmp_path).model_copy(
        update={
            "llm_enabled": True,
            "llm_provider": "groq",
            "gemini_api_key": None,
            "groq_api_key": None,
            "huggingface_api_key": None,
        }
    )
    return ModelGateway(settings)


def _response(status: int, headers: dict[str, str] | None = None, text: str = "{}") -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, text=text)


def test_parse_duration_forms():
    assert _parse_duration("120") == 120
    assert _parse_duration("7.5s") == 7.5
    assert abs(_parse_duration("2m59.56s") - 179.56) < 1e-9
    assert _parse_duration("") is None


def test_low_allowance_raises_a_warning(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    assert gw.status["alert"] is None
    headers = {
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "40",
        "x-ratelimit-reset-requests": "3h",
    }
    gw._observe("groq", _response(200, headers))
    usage = gw.usage("groq")
    assert usage["level"] == "low"
    assert usage["requests"] == {"limit": 1000, "remaining": 40, "resets_in": 10800}
    alert = gw.status["alert"]
    assert alert["level"] == "low" and "40 of 1000" in alert["message"]


def test_rate_limited_provider_is_exhausted_and_skipped(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch)
    gw._observe("groq", _response(429, {"retry-after": "30"}))
    assert gw.usage("groq")["level"] == "exhausted"
    assert all(spec.key != "groq" for spec in gw._order())
    alert = gw.status["alert"]
    assert alert["level"] == "exhausted" and "deterministic" in alert["message"]
    # A successful answer clears it.
    gw._observe("groq", _response(200))
    assert gw.usage("groq")["level"] == "ok"
