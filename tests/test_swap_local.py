"""The local swap protocol's reporting, on SIMULATED sessions (no camera, no window)."""

import importlib.util
import sys
from pathlib import Path

import pytest

from conftest import FakeDisplay
from lumen.decide import Thresholds
from lumen.session import Session, SessionConfig

PROTOCOL = Path(__file__).resolve().parents[1] / "experiments" / "swap_local" / "protocol.py"


@pytest.fixture(scope="module")
def protocol():
    spec = importlib.util.spec_from_file_location("swap_protocol", PROTOCOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["swap_protocol"] = module
    spec.loader.exec_module(module)
    return module


def test_report_compares_swap_on_with_swap_off(tmp_path, protocol):
    rows = []
    for label, scenario, swap in (("CF1", "genuine", False), ("CS1", "swap_inherit", True)):
        cfg = SessionConfig(source=f"synthetic:{scenario}", duration_s=8.0, warmup_s=1.0, segment_s=1.0,
                            out_dir=str(tmp_path / "s"), key_path=str(tmp_path / "k.pem"),
                            thresholds_path=str(tmp_path / "t.json"), label=label)
        r = Session(cfg, display_factory=lambda c: FakeDisplay(), thresholds=Thresholds(min_duration_s=3.0)).run()
        rows.append(protocol.summary_row(label, swap, r.record_path, str(tmp_path / "t.json")))

    genuine, swap = rows
    assert genuine["video_fps"] > 20  # simulated frames always change; a frozen source would read ~0
    assert genuine["cheek_contrast"] > swap["cheek_contrast"]
    table = protocol.markdown(rows)
    assert "| CS1 | ON |" in table and "| CF1 | off |" in table
    assert "No overlap" in protocol.contrast_summary(rows)


def test_contrast_summary_needs_both_kinds(protocol):
    assert "Not enough" in protocol.contrast_summary([{"swap": False, "cheek_contrast": 0.4}])
