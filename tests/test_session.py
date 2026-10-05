"""Whole sessions in real time against SIMULATED sources, with a fake display."""

import threading

import numpy as np
import pytest

from lumen.decide import Thresholds
from lumen.probe import EmissionLog, lab_to_chroma
from lumen.record import load_record, verify_record
from lumen.session import Session, SessionConfig, pose_summary, reanalyse, split_regions

FAST = Thresholds(min_duration_s=3.0, neck_gate_floor_multiple=2.0)


def config(tmp_path, **kw) -> SessionConfig:
    base = dict(source="synthetic:genuine", duration_s=8.0, warmup_s=1.0, segment_s=1.0,
                out_dir=str(tmp_path / "sessions"), key_path=str(tmp_path / "k.pem"),
                thresholds_path=str(tmp_path / "t.json"), label="test")
    return SessionConfig(**{**base, **kw})


@pytest.fixture(scope="module")
def genuine(tmp_path_factory):
    from conftest import FakeDisplay

    tmp = tmp_path_factory.mktemp("genuine")
    session = Session(config(tmp), display_factory=lambda c: FakeDisplay(), thresholds=FAST)
    return session, session.run()


def test_genuine_session_verifies(genuine):
    _, r = genuine
    assert r.outcome == "done"
    assert r.verdict.decision == "verify", r.verdict.reason
    assert r.verdict.provisional
    assert r.face_fraction > 0.95 and r.frames_processed > 150
    assert r.features.measurement.noise_floor == pytest.approx(4 / r.features.measurement.n_segments)
    assert r.features.cheek_contrast > 0.2
    assert r.pose is not None and abs(r.pose["yaw"]) < 0.05


def test_record_is_signed_simulated_and_reanalyses_identically(genuine):
    _, r = genuine
    rec = load_record(r.record_path)
    check = verify_record(rec)
    assert check.ok, check.problems
    assert rec["capture"]["simulated"] is True
    assert rec["probe"]["layout"] == "split"
    assert lab_to_chroma(rec["emitted"]["lab"]).shape[1] == 4
    again = reanalyse(rec)
    assert again.measurement.face.coherence == pytest.approx(r.features.measurement.face.coherence, abs=1e-3)
    assert again.cheek_contrast == pytest.approx(r.features.cheek_contrast, abs=1e-3)


def test_emission_csv_is_written_next_to_the_record(genuine):
    _, r = genuine
    cols = EmissionLog.load_csv(r.record_path.replace("record.json", "emission.csv"))
    assert {"a", "b", "a_r", "b_r"} <= set(cols)
    assert len(cols["t"]) == r.emitted_frames


def test_snapshot_after_the_run(genuine):
    session, _ = genuine
    snap = session.snapshot(since=0)
    assert snap["state"] == "done" and snap["layout"] == "split"
    assert snap["next"] == len(snap["samples"])
    probed = [row for row in snap["samples"] if row["probe"] is not None]
    assert probed and len(probed[0]["probe"]) == 4 and probed[0]["face"] is not None
    assert session.snapshot(since=snap["next"])["samples"] == []


def test_unopenable_source_abstains_without_crashing(tmp_path, fake_display):
    def broken(spec):
        raise RuntimeError("cannot open camera 7")

    r = Session(config(tmp_path, source="camera:7"), source_factory=broken,
                display_factory=lambda c: fake_display()).run()
    assert r.outcome == "error"
    assert r.verdict.decision == "abstain" and "cannot open source" in r.verdict.reason
    assert r.record_path is None


def test_operator_stop_ends_the_session_early(tmp_path, fake_display):
    session = Session(config(tmp_path, duration_s=20.0), display_factory=lambda c: fake_display(), thresholds=FAST)
    threading.Timer(2.0, session.stop).start()
    r = session.run()
    assert r.outcome == "stopped"
    assert "ended early" in " ".join(r.notes)
    assert r.verdict.decision == "abstain"  # ~1 s of probe is not enough to decide


def test_split_regions_drops_rare_optional_regions():
    t = np.arange(10.0)
    lab = np.array([60.0, 10.0, 10.0])
    samples = [{"face": lab, "neck": lab, "background": lab} if i < 5 else {"face": lab, "neck": lab}
               for i in range(9)] + [None]
    regions, fraction = split_regions(t, samples)
    assert fraction == pytest.approx(0.9)
    assert set(regions) == {"face", "neck"}
    assert regions["face"][1].shape == (9, 3)


def test_pose_summary():
    assert pose_summary([None, None]) is None
    s = pose_summary([(0.1, 2.0, 40.0), None, (0.3, 4.0, 42.0), (0.2, 3.0, 41.0)])
    assert s["yaw"] == pytest.approx(0.2) and s["n"] == 3 and s["ipd_px"] == pytest.approx(41.0)
