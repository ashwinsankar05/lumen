"""The console API end to end, with SIMULATED sessions and a fake display."""

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from conftest import FakeDisplay
from lumen.api import create_app
from lumen.decide import Thresholds
from lumen.session import Session, SessionConfig

FAST = Thresholds(min_duration_s=3.0, neck_gate_floor_multiple=2.0)


@pytest.fixture
def client(tmp_path):
    base = SessionConfig(source="synthetic:genuine", duration_s=6.0, warmup_s=1.0, segment_s=1.0,
                         out_dir=str(tmp_path / "sessions"), key_path=str(tmp_path / "k.pem"),
                         thresholds_path=str(tmp_path / "t.json"))

    def factory(cfg):
        return Session(replace(cfg, segment_s=1.0), display_factory=lambda c: FakeDisplay(), thresholds=FAST)

    return TestClient(create_app(base, session_factory=factory))


def run_session(client, **body) -> dict:
    sid = client.post("/api/sessions", json=body).json()["session_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        snap = client.get(f"/api/sessions/{sid}/live").json()
        if "result" in snap:
            return snap["result"]
        time.sleep(0.2)
    raise AssertionError("session did not finish")


def test_health_config_and_console(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["thresholds_provisional"] and len(h["public_key"]) == 64
    assert client.get("/api/config").json()["layout"] == "split"
    page = client.get("/")
    assert page.status_code == 200 and "LUMEN" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_session_record_verify_and_listing(client):
    result = run_session(client, label="api-genuine")
    assert result["verdict"]["decision"] == "verify", result["verdict"]["reason"]
    sid = result["session_id"]

    rows = client.get("/api/sessions").json()
    assert rows[0]["session_id"] == sid and rows[0]["simulated"] and rows[0]["layout"] == "split"
    assert client.get(f"/api/sessions/{sid}").json()["state"] == "done"

    record = client.get(f"/api/sessions/{sid}/record").json()
    assert client.post("/api/records/verify", json=record).json()["ok"]
    record["verdict"]["decision"] = "reject"
    assert not client.post("/api/records/verify", json=record).json()["ok"]


def test_calibrate_then_reset(client):
    ids = [run_session(client, label=f"g{i}")["session_id"] for i in range(3)]
    th = client.post("/api/calibrate", json={"session_ids": ids}).json()
    assert th["n_genuine"] == 3 and not th["provisional"] and th["cheek_contrast_min"] is not None
    assert client.get("/api/thresholds").json() == th
    assert client.delete("/api/thresholds").json()["provisional"]


def test_bad_requests(client):
    assert client.post("/api/sessions", json={"nonsense": 1}).status_code == 422
    assert client.post("/api/calibrate", json={}).status_code == 422
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/nope/live").status_code == 404
    assert client.get("/api/sessions/..%5C..%5Csecret/record").status_code in (400, 404)
