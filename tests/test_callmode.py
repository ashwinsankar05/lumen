"""Call mode: the probe as our outgoing video (virtual camera), and the subject's tile as the source.

No real virtual-camera driver is needed: a fake camera stands in for pyvirtualcam.
"""

from dataclasses import replace

import numpy as np
import pytest

from lumen.capture import roi_to_spec
from lumen.decide import Thresholds, calibrate, decide
from lumen.emitter import VirtualCamDisplay, render_field
from lumen.session import Session, SessionConfig
from test_decide import features


class FakeCamera:
    instances: list["FakeCamera"] = []

    def __init__(self, *, width, height, fps, device=None):
        self.width, self.height, self.fps = width, height, fps
        self.device = device or "Fake Virtual Camera"
        self.frames: list[np.ndarray] = []
        self.closed = False
        FakeCamera.instances.append(self)

    def send(self, rgb):
        assert rgb.shape == (self.height, self.width, 3) and rgb.dtype == np.uint8
        self.frames.append(rgb)

    def close(self):
        self.closed = True


def no_driver(**kwargs):
    raise RuntimeError("'obs' backend: virtual camera output could not be started")


def test_virtual_camera_gets_rgb_frames_with_the_same_mean_colour():
    d = VirtualCamDisplay(size=(320, 180), fps=30.0, camera_factory=FakeCamera)
    field = render_field(np.array([[0.7, 0.6, 0.5], [0.5, 0.6, 0.7]]))
    assert d.show(field) == -1
    sent = FakeCamera.instances[-1].frames[-1]
    left, right = sent[:, :160].reshape(-1, 3).mean(0), sent[:, 160:].reshape(-1, 3).mean(0)
    np.testing.assert_allclose(left / 255, [0.7, 0.6, 0.5], atol=1 / 255 / 8)   # RGB order, left half
    np.testing.assert_allclose(right / 255, [0.5, 0.6, 0.7], atol=1 / 255 / 8)
    d.close()
    assert FakeCamera.instances[-1].closed
    assert "Fake Virtual Camera" in d.notes[0]


def test_missing_driver_explains_what_to_install():
    with pytest.raises(RuntimeError, match="OBS"):
        VirtualCamDisplay(camera_factory=no_driver)


def base_config(tmp_path, **kw):
    cfg = SessionConfig(source="synthetic:genuine", duration_s=5.0, warmup_s=1.0, segment_s=1.0,
                        out_dir=str(tmp_path / "s"), key_path=str(tmp_path / "k.pem"),
                        thresholds_path=str(tmp_path / "t.json"))
    return replace(cfg, **kw)


def test_call_mode_session_streams_the_probe_at_30_fps(tmp_path):
    cfg = base_config(tmp_path, emit="virtualcam", max_lag_s=2.0)
    r = Session(cfg, display_factory=lambda c: VirtualCamDisplay(size=(320, 180), camera_factory=FakeCamera),
                thresholds=Thresholds(min_duration_s=2.0)).run()
    assert r.outcome == "done"
    assert 120 < r.emitted_frames < 170  # ~5 s at 30 fps, not 60
    assert any("virtual camera" in n for n in r.notes)


def test_session_without_a_probe_output_abstains_cleanly(tmp_path):
    def broken(cfg):
        raise RuntimeError("no virtual camera available")

    r = Session(base_config(tmp_path, emit="virtualcam"), display_factory=broken).run()
    assert r.outcome == "error"
    assert r.verdict.decision == "abstain" and "probe output" in r.verdict.reason


def test_unknown_emit_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="emit"):
        Session(base_config(tmp_path, emit="projector"))


def test_mirrored_call_video_is_learnt_from_genuine_sessions():
    """A mirrored call flips every genuine cheek contrast; a swap must still be rejected."""
    genuine = [features(contrast=c) for c in (-0.30, -0.34, -0.28)]
    th = calibrate(genuine)
    assert th.cheek_contrast_sign == -1 and th.cheek_contrast_min > 0
    assert all(decide(f, th).decision == "verify" for f in genuine)
    v = decide(features(contrast=0.02), th)
    assert v.decision == "reject" and "L3" in v.reason
    assert "mirrored" in next(c.threshold for c in v.checks if c.name == "L3_cheek_contrast")


def test_roi_to_spec_maps_back_to_absolute_screen_pixels():
    monitor = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
    assert roi_to_spec((100, 50, 400, 225), monitor, scale=0.5) == "screen:2120,100,800,450"
    assert roi_to_spec((0, 0, 0, 0), monitor, scale=1.0) is None
