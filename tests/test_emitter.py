import threading

import numpy as np
import pytest

from lumen.colour import lab_to_srgb
from lumen.emitter import Emitter, bayer, render_field
from lumen.probe import EmissionLog, Probe


def test_bayer_is_a_permutation():
    assert sorted(bayer(8).ravel()) == list(range(64))


@pytest.mark.parametrize("value", [0.0, 0.1234, 0.5, 0.77777, 1.0])
def test_dithered_field_mean_keeps_sub_code_value_precision(value):
    field = render_field(np.array([value, 0.3, 0.6]), shape=(64, 64))
    mean_r = field[..., 2].mean() / 255
    assert mean_r == pytest.approx(value, abs=1 / 255 / 32)


def test_field_channel_order_is_bgr():
    field = render_field(np.array([1.0, 0.0, 0.0]))
    assert field[..., 2].min() == 255 and field[..., 0].max() == 0


def test_split_field_renders_each_half():
    rgb = Probe("split", duration_s=20.0).srgb_at(7.3)
    field = render_field(rgb)
    half = field.shape[1] // 2
    left = field[:, :half].reshape(-1, 3).mean(axis=0)[::-1] / 255
    right = field[:, half:].reshape(-1, 3).mean(axis=0)[::-1] / 255
    np.testing.assert_allclose(left, rgb[0], atol=1 / 255 / 8)
    np.testing.assert_allclose(right, rgb[1], atol=1 / 255 / 8)


class StepClock:
    def __init__(self, start=0.0, step=1 / 60):
        self.t, self.step = start, step

    def __call__(self):
        self.t += self.step / 2  # the emitter reads the clock twice per frame
        return self.t


def test_emitter_shows_base_then_logs_every_probe_frame(fake_display):
    probe = Probe("emit", duration_s=2.0)
    log = EmissionLog(probe, t0=1.0)
    display = fake_display()
    outcome = Emitter(log, display, now=StepClock()).run()

    assert outcome == "done"
    assert 100 < len(log) < 130  # ~2 s at 60 Hz
    assert np.all(log.t >= 1.0) and np.all(log.t < 3.0)
    # before t0: the neutral base colour, identical on both halves
    np.testing.assert_array_equal(display.frames[0], render_field(lab_to_srgb(probe.base_lab())))
    assert np.array_equal(display.frames[0][:, :80], display.frames[0][:, 80:])


def test_emitter_escape_and_stop(fake_display):
    probe = Probe("emit", duration_s=5.0)
    assert Emitter(EmissionLog(probe, 0.0), fake_display(press_at=3), now=StepClock()).run() == "escape"
    stop = threading.Event()
    stop.set()
    assert Emitter(EmissionLog(probe, 0.0), fake_display(), now=StepClock()).run(stop) == "stopped"
