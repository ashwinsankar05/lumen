"""The verification link: browser probe code against probe.py, and the server end to end on
SIMULATED frames (no browser, no camera)."""

import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from lumen.colour import lab_to_srgb
from lumen.probe import Probe
from lumen.record import load_record, verify_record
from lumen.regions import RegionExtractor
from lumen.synthetic import FixedDetector, SyntheticScene
from lumen.web import WebSession, create_app, ensure_certificate
from lumen.session import SessionConfig

PROBE_JS = Path(__file__).resolve().parents[1] / "webverify" / "probe.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_browser_probe_matches_probe_py(tmp_path):
    s = WebSession(SessionConfig(source="browser", duration_s=20.0, amplitude=34.0, out_dir=str(tmp_path)))
    params = s.page_params()
    s.finish(0.0, [])  # stop the worker thread
    times = [0.0, 0.0137, 3.3, 7.77, 12.5, 19.999]
    script = (f"const p = require({json.dumps(str(PROBE_JS))});"
              f"const probe = {json.dumps(params)};"
              f"const times = {json.dumps(times)};"
              "console.log(JSON.stringify({lab: times.map(t => p.labAt(probe, t)),"
              " srgb: [p.labToSrgb(70, 20, -15), p.labToSrgb(70, -34, 34)]}));")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    expected = s.probe.lab_at(np.array(times))           # (N, 2, 3)
    assert np.abs(np.array(out["lab"]) - expected).max() < 1e-9
    assert np.abs(np.array(out["srgb"]) - lab_to_srgb(np.array([[70, 20, -15], [70, -34, 34]]))).max() < 1e-12


class _PageProbe:
    """A probe rebuilt from what the page receives (it never sees the code itself)."""

    def __init__(self, params):
        self.layout, self.duration_s = params["layout"], params["duration_s"]
        # used only by the simulated replay to build its "earlier session" probe from a fixed code,
        # which is out of gamut at 34; that old recording's own amplitude does not matter
        self.amplitude = 24.0
        self.seqs = params["sequences"]
        self.n_channels = 2 * len(self.seqs)

    def chroma_at(self, t):
        out = []
        for s in self.seqs:
            arg = 2 * np.pi * float(t) * np.array(s["freqs"])
            out += [s["scale_a"] * np.cos(arg + s["phase_a"]).sum(), s["scale_b"] * np.cos(arg + s["phase_b"]).sum()]
        return np.array(out)


def _run_page(client, scenario, t0=100.0, fps=30.0, shuffle=False):
    """What the page does: start, upload camera frames with capture times, report draw times, finish."""
    params = client.post("/api/sessions", json={"label": f"test-{scenario}"}).json()
    probe = _PageProbe(params)
    scene = SyntheticScene(scenario, lambda: (probe, t0), seed=3)
    times = np.arange(t0 - 1.0, t0 + params["duration_s"] + 0.5, 1 / fps)
    if shuffle:  # parallel uploads: neighbouring frames swap places on the way
        times = times.copy()
        swap = np.arange(5, len(times) - 1, 40)
        times[swap], times[swap + 1] = times[swap + 1].copy(), times[swap].copy()
    for t in times:
        ok, jpg = cv2.imencode(".jpg", scene.render(t), [cv2.IMWRITE_JPEG_QUALITY, 92])
        r = client.post(f"/api/sessions/{params['session_id']}/frame", content=jpg.tobytes(),
                        headers={"X-Capture-T": repr(float(t))})
        assert r.status_code == 204
    drawn = np.arange(t0, t0 + params["duration_s"], 1 / 60).tolist()
    return client.post(f"/api/sessions/{params['session_id']}/finish",
                       json={"t0": t0, "drawn_t": drawn, "client": {"camera": "simulated", "simulated": True}})


@pytest.fixture()
def client(tmp_path):
    app = create_app(out_dir=str(tmp_path / "sessions"), thresholds_path=str(tmp_path / "t.json"),
                     deep_thresholds_path=str(tmp_path / "deep.json"), key_path=str(tmp_path / "k.pem"),
                     duration_s=30.0, save_video=True, extractor_factory=lambda: RegionExtractor(FixedDetector()))
    return TestClient(app)


def test_genuine_session_through_the_link_verifies_and_signs(client, tmp_path):
    r = _run_page(client, "genuine")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "verify", body["reason"]
    assert body["cheek_contrast"] > 0.2
    assert "deep" not in body or body["deep_check"]
    rec = client.get(body["record"]).json()
    check = verify_record(rec)
    assert check.ok, check.problems              # the server regenerated every drawn colour from the code
    assert rec["capture"]["simulated"] is True
    folder = tmp_path / "sessions" / body["session_id"]
    assert (folder / "video.mkv").exists() and (folder / "video_timestamps.csv").exists()


def test_frames_arriving_out_of_order_are_put_in_time_order(client):
    """Measured through OBS Virtual Camera: 5 and 19 of 2700 frames arrived out of order."""
    body = _run_page(client, "genuine", shuffle=True).json()
    assert body["verdict"] == "verify", body["reason"]
    assert any("out of order" in n for n in body["notes"])
    assert not body["deep_check"].startswith("not measurable")
    assert body["temporal"].split(":")[0] in ("DIRECT", "PIPELINE", "ABSTAIN")


def test_time_ordered_fixes_an_unsorted_record():
    from lumen.web import time_ordered

    rec = {"returned": {"t": [1.0, 3.0, 2.0, 3.0], "regions": {"face": ["a", "c", "b", "c2"]},
                        "pose": [1, 3, 2, 4]}}
    out = time_ordered(rec)
    assert out["returned"]["t"] == [1.0, 2.0, 3.0]
    assert out["returned"]["regions"]["face"] == ["a", "b", "c"]
    assert rec["returned"]["t"] == [1.0, 3.0, 2.0, 3.0]  # the signed original is not touched


def test_replayed_video_through_the_link_is_rejected(client):
    body = _run_page(client, "replay").json()
    assert body["verdict"] == "reject" and "replay" in body["reason"]


def test_bad_requests(client):
    assert client.post("/api/sessions/nope/frame", content=b"x", headers={"X-Capture-T": "1"}).status_code == 404
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    assert client.post(f"/api/sessions/{sid}/frame", content=b"x").status_code == 400  # no capture time
    assert client.get("/static/../src/lumen/web.py").status_code == 404
    assert client.get("/static/secret.txt").status_code == 404
    assert client.get("/api/sessions/nope/record").status_code == 404
    assert client.post(f"/api/sessions/{sid}/finish", json={"drawn_t": []}).status_code == 400  # no t0


def test_page_and_scripts_are_served(client):
    assert "LUMEN presence check" in client.get("/").text
    for name in ("verify.js", "probe.js", "verify.css"):
        assert client.get(f"/static/{name}").status_code == 200


def test_self_signed_certificate_covers_this_laptops_addresses(tmp_path):
    from cryptography import x509

    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    ensure_certificate(cert, key, ["192.168.1.20"])
    c = x509.load_pem_x509_certificate(cert.read_bytes())
    ips = {str(i) for i in c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
           .get_values_for_type(x509.IPAddress)}
    assert ips == {"127.0.0.1", "192.168.1.20"}
    before = cert.read_bytes()
    ensure_certificate(cert, key, ["192.168.1.20"])
    assert cert.read_bytes() == before            # reused while still valid


def test_neck_check_returns_outlines_and_flags_a_dark_neck(client):
    """The pre-flight neck check on SIMULATED frames: a normal neck passes, a neck region darkened
    like a collar (measured 2026-10-07: face/neck lightness 1.6-2.2) is flagged."""
    probe = _PageProbe(client.post("/api/sessions", json={}).json())
    scene = SyntheticScene("genuine", lambda: (probe, 100.0), seed=5)
    img = scene.render(99.0)
    r = client.post("/api/check-frame", content=cv2.imencode(".jpg", img)[1].tobytes()).json()
    assert r["face"] and r["ok"] and {"face", "neck"} <= set(r["outlines"])
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in r["outlines"]["neck"])
    dark = img.copy()
    xs, ys = zip(*[(x * img.shape[1], y * img.shape[0]) for x, y in r["outlines"]["neck"]])
    dark[int(min(ys)):int(max(ys)) + 1, int(min(xs)):int(max(xs)) + 1] //= 3   # a black collar over the neck
    r2 = client.post("/api/check-frame", content=cv2.imencode(".jpg", dark)[1].tobytes()).json()
    assert r2["face"] and not r2["ok"] and "clothing" in r2["hint"]
    assert client.post("/api/check-frame", content=b"not a jpeg").status_code == 400


def test_pick_calibration_first_matching_rule_wins():
    from lumen.web import Calibration, pick_calibration

    cals = [Calibration("obs", "a.json", "da.json", camera="OBS", remote=True),
            Calibration("remote webcam", "b.json", "db.json", remote=True),
            Calibration("this laptop", "c.json", "dc.json")]
    assert pick_calibration(cals, "OBS Virtual Camera", remote=True).name == "obs"
    assert pick_calibration(cals, "obs virtual camera", remote=True).name == "obs"       # case-insensitive
    assert pick_calibration(cals, "Integrated Camera (5986:11ad)", remote=True).name == "remote webcam"
    assert pick_calibration(cals, "OBS Virtual Camera", remote=False).name == "this laptop"
    assert pick_calibration(cals, "", remote=False).name == "this laptop"


def test_each_session_is_judged_by_its_camera_paths_calibration(tmp_path):
    """Every calibration carries L4, so a camera label can only choose between genuine calibrations."""
    import json as _json

    from lumen.decide import Thresholds
    from lumen.web import Calibration

    for name, limit in (("obs", 1.9), ("cam", 1.2)):
        Thresholds(face_neck_lightness_max=limit, pipeline_lag_max_s=0.25).save(tmp_path / f"{name}.json")
    cals = [Calibration("obs path", str(tmp_path / "obs.json"), str(tmp_path / "d1.json"), camera="OBS"),
            Calibration("webcam", str(tmp_path / "cam.json"), str(tmp_path / "d2.json"))]
    app = create_app(out_dir=str(tmp_path / "s"), key_path=str(tmp_path / "k.pem"), duration_s=30.0,
                     save_video=False, extractor_factory=lambda: RegionExtractor(FixedDetector()), calibrations=cals)
    c = TestClient(app)
    obs = c.post("/api/sessions", json={"camera": "OBS Virtual Camera"}).json()
    cam = c.post("/api/sessions", json={"camera": "Integrated Camera"}).json()
    assert (obs["calibration"], cam["calibration"]) == ("obs path", "webcam")
    for p in (obs, cam):
        rec = c.post(f"/api/sessions/{p['session_id']}/finish", json={"t0": 0.0, "drawn_t": [0.0, 0.1, 0.2]})
        assert rec.status_code == 200
    saved = {d.name: _json.loads((d / "record.json").read_text()) for d in (tmp_path / "s").iterdir()
             if (d / "record.json").exists()}
    limits = sorted(r["thresholds"]["face_neck_lightness_max"] for r in saved.values())
    assert limits == [1.2, 1.9]
    assert all(any(n.startswith("calibration: ") for n in r["extra"]["notes"]) for r in saved.values())
    assert c.get("/api/info").json()["calibrations"][0]["name"] == "obs path"


def test_a_visitor_through_a_cloudflare_tunnel_counts_as_another_laptop():
    from types import SimpleNamespace

    from lumen.web import _is_remote

    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    tunnelled = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"CF-Connecting-IP": "203.0.113.7"})
    lan = SimpleNamespace(client=SimpleNamespace(host="10.164.128.150"), headers={})
    assert (_is_remote(local), _is_remote(tunnelled), _is_remote(lan)) == (False, True, True)
