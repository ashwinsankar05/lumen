"""The codec-survival experiment's plumbing (SIMULATED frames). Skipped without PyAV."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("av")

RUN = Path(__file__).resolve().parents[1] / "experiments" / "codec_survival" / "run.py"


@pytest.fixture(scope="module")
def codec_run():
    import sys

    spec = importlib.util.spec_from_file_location("codec_run", RUN)
    module = importlib.util.module_from_spec(spec)
    sys.modules["codec_run"] = module  # dataclasses look their module up while being created
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["h264-300k", "vp8-300k"])
def test_roundtrip_keeps_every_frame_and_the_mean_colour(codec_run, name):
    codec = next(c for c in codec_run.CODECS if c.name == name)
    frames = [np.full((240, 320, 3), (90, 120, 160), np.uint8) for _ in range(45)]
    out = list(codec_run.roundtrip(iter(frames), codec, 30.0))
    assert len(out) == 45 and out[0].shape == (240, 320, 3)
    np.testing.assert_allclose(out[-1].reshape(-1, 3).mean(axis=0), (90, 120, 160), atol=3)


def test_uncompressed_reference_passes_frames_through(codec_run):
    frames = [np.zeros((4, 4, 3), np.uint8)] * 3
    assert list(codec_run.roundtrip(iter(frames), codec_run.CODECS[0], 30.0)) == frames


def test_synthetic_case_reports_a_row(codec_run):
    codec = next(c for c in codec_run.CODECS if c.name == "h264-300k")
    row = codec_run.synthetic_case("genuine", codec, duration=25.0)
    assert row["case"].startswith("SIMULATED") and row["codec"] == "h264-300k"
    assert row["neck_coherence"] > 2 * row["noise_floor"]
    assert "± " in codec_run.markdown([row])
