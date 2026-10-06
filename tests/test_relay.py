"""relay.py: a real local HTTP receiver and sender, on SIMULATED session records."""

import copy
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from conftest import FakeDisplay
from lumen.decide import Thresholds
from lumen.record import load_record, save_record
from lumen.relay import Receiver, make_handler, ping, send_record
from lumen.session import Session, SessionConfig


def _session(tmp_path, label, key="k.pem"):
    cfg = SessionConfig(source="synthetic:genuine", duration_s=8.0, warmup_s=1.0, segment_s=1.0,
                        out_dir=str(tmp_path / "sender"), key_path=str(tmp_path / key),
                        thresholds_path=str(tmp_path / "t.json"), label=label)
    r = Session(cfg, display_factory=lambda c: FakeDisplay(), thresholds=Thresholds(min_duration_s=3.0)).run()
    return r.record_path


@pytest.fixture()
def receiver(tmp_path):
    rx = Receiver(tmp_path / "remote", token="test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(rx))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield rx, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_ping_needs_the_token(receiver):
    _, url = receiver
    assert ping(url, "test-token") == {"ok": True}
    assert "wrong token" in ping(url, "guess")["error"]


def test_a_signed_record_is_received_checked_and_saved(tmp_path, receiver):
    rx, url = receiver
    path = _session(tmp_path, "deep-check")
    reply = send_record(url, "test-token", path)
    assert "error" not in reply, reply
    sid = load_record(path)["session_id"]
    assert (rx.out_dir / sid / "record.json").exists()
    assert rx.key_file.read_text() == load_record(path)["signature"]["public_key"]
    assert "not calibrated yet" in reply["deep"] and "SIMULATED" in reply["deep"]
    assert sid in rx.results.read_text(encoding="utf-8")


def test_wrong_token_is_refused(tmp_path, receiver):
    _, url = receiver
    assert "wrong token" in send_record(url, "guess", _session(tmp_path, "x"))["error"]


def test_a_tampered_record_is_refused(tmp_path, receiver):
    _, url = receiver
    rec = load_record(_session(tmp_path, "x"))
    bad = copy.deepcopy(rec)
    bad["verdict"]["decision"] = "verify" if rec["verdict"]["decision"] != "verify" else "reject"
    path = save_record(bad, tmp_path / "bad.json")
    reply = send_record(url, "test-token", path)
    assert reply["error"] == "record refused" and any("signature" in p for p in reply["problems"])


def test_after_the_first_record_another_key_is_refused(tmp_path, receiver):
    _, url = receiver
    assert "error" not in send_record(url, "test-token", _session(tmp_path, "a", key="k1.pem"))
    reply = send_record(url, "test-token", _session(tmp_path, "b", key="k2.pem"))
    assert reply["error"] == "record refused" and any("unexpected key" in p for p in reply["problems"])


def test_unsafe_session_id_is_refused(tmp_path, receiver):
    rx, url = receiver
    rec = load_record(_session(tmp_path, "x"))
    rec["session_id"] = "../../evil"
    reply = send_record(url, "test-token", save_record(rec, tmp_path / "evil.json"))
    assert "unsafe" in reply["error"]
    assert not (tmp_path / "evil").exists()


def test_calibrates_itself_after_three_genuine_sessions(tmp_path, receiver):
    rx, url = receiver
    notes = [send_record(url, "test-token", _session(tmp_path, "deep-genuine"))["note"] for _ in range(3)]
    assert notes[0] == "genuine calibration session 1 of 3"
    assert notes[2].startswith("calibrated from 3 genuine sessions")
    assert rx.th_file.exists()
    reply = send_record(url, "test-token", _session(tmp_path, "deep-check"))
    assert "needs >=" in reply["deep"]  # judged against the new bounds
    assert json.loads(rx.th_file.read_text())["n_genuine"] == 3
