"""Interceptor engagement: the trajectory check must be quiet when healthy and
every injected fault or attack must be detected and attributed correctly."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import routes_intercept
from telemetry.intercept import FAULTS, EngagementConfig, simulate_engagement

GEOMETRIES = [
    EngagementConfig(),
    EngagementConfig(target_x_km=60, target_y_km=20),
    EngagementConfig(target_x_km=200, target_y_km=100, target_vx_kms=-3, target_vy_kms=-1),
    EngagementConfig(target_weave_kms2=0.05),
]


@pytest.mark.parametrize("cfg", GEOMETRIES)
def test_nominal_engagement_intercepts_without_alarms(cfg):
    result = simulate_engagement(cfg)
    assert result["preflight"]["go"]
    assert result["outcome"]["result"] == "INTERCEPT"
    assert result["events"] == []
    assert result["diagnosis"] == []


@pytest.mark.parametrize("key", sorted(FAULTS))
def test_every_anomaly_is_detected_after_onset_and_attributed(key):
    result = simulate_engagement(EngagementConfig(fault=key))
    detection = result["detection"]
    assert detection["first_alert_s"] is not None, "anomaly went undetected"
    assert detection["first_alert_s"] >= detection["onset_s"]
    assert detection["correct"], f"diagnosed {detection['diagnosed']} for {key}"


@pytest.mark.parametrize("key", sorted(FAULTS))
def test_attribution_holds_across_severity_and_onset(key):
    for severity in (0.4, 0.7, 1.0):
        for onset in (6.0, 18.0):
            result = simulate_engagement(EngagementConfig(fault=key, severity=severity, onset_s=onset))
            assert result["detection"]["correct"], (severity, onset, result["detection"])


def test_link_authentication_neutralises_forged_target_updates():
    protected = simulate_engagement(EngagementConfig(fault="command_injection", link_authentication=True))
    exposed = simulate_engagement(EngagementConfig(fault="command_injection", link_authentication=False))
    assert protected["outcome"]["result"] == "INTERCEPT"
    assert exposed["outcome"]["result"] == "MISS"


def test_simulation_is_deterministic():
    cfg = EngagementConfig(fault="uplink_jamming", seed=11)
    assert simulate_engagement(cfg) == simulate_engagement(EngagementConfig(fault="uplink_jamming", seed=11))


def test_api_rejects_unknown_fault_and_serves_catalog():
    app = FastAPI()
    app.include_router(routes_intercept.router)
    client = TestClient(app)
    assert len(client.get("/intercept/faults").json()["faults"]) == len(FAULTS)
    assert client.post("/intercept/simulate", json={"fault": "nope"}).status_code == 422
    ok = client.post("/intercept/simulate", json={"fault": "gnss_spoofing", "severity": 0.8})
    assert ok.status_code == 200 and ok.json()["detection"]["diagnosed"] == "gnss_spoofing"
