"""The M1 report script on SIMULATED records (plumbing only)."""

import importlib.util
from pathlib import Path

import pytest

from conftest import FakeDisplay
from lumen.decide import Thresholds
from lumen.session import Session, SessionConfig

REPORT = Path(__file__).resolve().parents[1] / "experiments" / "m1_probe_survival" / "report.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("m1_report", REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_tabulates_and_flags_simulated(tmp_path, report):
    cfg = SessionConfig(source="synthetic:genuine", duration_s=6.0, warmup_s=1.0, segment_s=1.0,
                        out_dir=str(tmp_path), key_path=str(tmp_path / "k.pem"), label="g")
    fast = Thresholds(min_duration_s=3.0)
    ids = [Session(cfg, display_factory=lambda c: FakeDisplay(), thresholds=fast).run().session_id for _ in range(2)]

    records = {sid: report.load(sid, tmp_path) for sid in ids}
    rows = report.rows_for(records, genuine=ids[:1])
    assert [r["thresholds"] for r in rows] == ["leave-one-out", "1 genuine"]
    assert all(r["simulated"] and r["layout"] == "split" for r in rows)
    table = report.markdown(rows)
    assert "SIMULATED" in table and ids[0] in table
