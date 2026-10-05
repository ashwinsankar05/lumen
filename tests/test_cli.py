import json

from lumen.__main__ import main
from lumen.probe import EmissionLog, Probe
from lumen.record import build_record, load_or_create_key, save_record, sign_record


def signed_record_file(tmp_path):
    probe = Probe("cli", duration_s=5.0)
    log = EmissionLog(probe, t0=0.0)
    for i in range(1, 200):
        log.emit(i / 60)
    rec = build_record(session_id="cli", started_at="2026-10-05T00:00:00+00:00", nonce="cli", log=log,
                       capture_t=[], regions={}, capture={}, features=None, verdict={}, thresholds={})
    return save_record(sign_record(rec, load_or_create_key(tmp_path / "k.pem")), tmp_path / "r.json")


def test_verify_command(tmp_path, capsys):
    path = signed_record_file(tmp_path)
    assert main(["verify", str(path)]) == 0
    assert "signature valid:            True" in capsys.readouterr().out

    rec = json.loads(path.read_text())
    rec["probe"]["nonce"] = "other"
    path.write_text(json.dumps(rec))
    assert main(["verify", str(path)]) == 1


def test_reanalyse_command_abstains_without_returned_data(tmp_path, capsys, monkeypatch):
    path = signed_record_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["reanalyse", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["features"] is None and out["verdict"]["decision"] == "abstain"
