"""decide.py on hand-built Features: every branch, every gate, and calibration."""

from dataclasses import replace

import pytest

from lumen.decide import Thresholds, calibrate, decide, load_thresholds
from lumen.features import Features, Measurement, RegionResponse


def features(*, face=0.85, neck=0.8, gain=1.1, floor=4 / 57, duration=60.0, gap=0.05, face_neck=0.9,
             background=0.1, contrast=0.45, lightness=1.05) -> Features:
    m = Measurement(
        face=RegionResponse(face, (face, face), 1.0 * gain),
        neck=RegionResponse(neck, (neck, neck), 1.0),
        differential_gain=gain, lag_s=0.1, band_hz=(2.0, 8.0), fs=30.0, duration_s=duration,
        n_segments=57, noise_floor=floor, max_gap_s=gap,
    )
    lateral = {} if contrast is None else {"face.cheek_r": contrast / 2, "face.cheek_l": -contrast / 2}
    return Features(measurement=m, face_neck_coherence=face_neck, background_gain_ratio=background,
                    lateral=lateral, cheek_contrast=contrast, face_neck_lightness=lightness)


CALIBRATED = Thresholds(provisional=False, cheek_contrast_min=0.35, max_abs_yaw=0.2, face_neck_lightness_max=1.15)


def test_genuine_verifies_with_all_three_layers():
    v = decide(features(), CALIBRATED, pose={"yaw": 0.02})
    assert v.decision == "verify"
    assert "lean" in v.reason
    assert not v.provisional
    assert {c.name: c.passed for c in v.checks}["L3_cheek_contrast"] is True


def test_provisional_thresholds_say_so_and_skip_l3():
    v = decide(features())
    assert v.decision == "verify" and v.provisional
    assert "L3 not evaluated" in v.reason


def test_single_probe_reports_l3_unavailable():
    v = decide(features(contrast=None), CALIBRATED)
    l3 = next(c for c in v.checks if c.name == "L3_cheek_contrast")
    assert l3.passed is None and "two-sided" in l3.threshold
    assert v.decision == "verify"


@pytest.mark.parametrize("kwargs, layer", [
    (dict(face=0.1), "L1"),
    (dict(gain=0.1), "L2"),
    (dict(gain=4.0), "L2"),
    (dict(background=0.9), "falloff"),
    (dict(contrast=0.2), "L3"),
])
def test_each_layer_rejects(kwargs, layer):
    v = decide(features(**kwargs), CALIBRATED)
    assert v.decision == "reject"
    assert layer in v.reason


def test_replay_is_rejected_when_neither_follows_today_but_they_follow_each_other():
    v = decide(features(face=0.05, neck=0.04, face_neck=0.8), CALIBRATED)
    assert v.decision == "reject" and "replay" in v.reason


def test_weak_but_real_response_is_not_called_a_replay():
    """Measured through a Meet call: neck 0.250 at floor 0.070 (3.6x), face-neck 0.737."""
    v = decide(features(face=0.282, neck=0.250, floor=0.070, face_neck=0.737), CALIBRATED)
    assert v.decision == "abstain" and "too weak" in v.reason


def test_measured_replay_is_still_rejected():
    """Measured R1: neck 0.094 at floor 0.078 (1.2x), face-neck 0.867."""
    v = decide(features(face=0.092, neck=0.094, floor=0.078, face_neck=0.867), CALIBRATED)
    assert v.decision == "reject" and "replay" in v.reason


def test_no_probe_signal_abstains():
    v = decide(features(face=0.05, neck=0.04, face_neck=0.1), CALIBRATED)
    assert v.decision == "abstain" and "neck reference" in v.reason


def test_neck_gate_scales_with_noise_floor():
    th = Thresholds()
    assert th.neck_gate(0.01) == th.neck_gate_min
    assert th.neck_gate(0.1) == pytest.approx(0.4)


@pytest.mark.parametrize("kwargs, extra, needle", [
    (dict(duration=10.0), {}, "duration_s"),
    (dict(gap=2.0), {}, "max_gap_s"),
    ({}, dict(face_fraction=0.5), "face_fraction"),
    ({}, dict(capture_error="camera unplugged"), "capture failed"),
    ({}, dict(pose={"yaw": 0.4}), "head turned"),
    (dict(lightness=1.4), {}, "lamp"),
])
def test_abstain_gates(kwargs, extra, needle):
    v = decide(features(**kwargs), CALIBRATED, **extra)
    assert v.decision == "abstain"
    assert needle in v.reason


def test_subject_much_farther_than_calibration_abstains():
    """Measured case: sitting back shrank the eye distance 30 -> 22 px and the cheek lean to +0.265."""
    th = calibrate([features(), features(), features()],
                   poses=[{"yaw": 0.0, "yaw_iqr": 0.02, "ipd_px": p} for p in (29.7, 31.0, 32.3)])
    assert th.min_ipd_px == pytest.approx(0.85 * 29.7, abs=0.01)
    v = decide(features(contrast=0.265), th, pose={"yaw": 0.0, "ipd_px": 21.8})
    assert v.decision == "abstain" and "farther" in v.reason
    assert decide(features(), th, pose={"yaw": 0.0, "ipd_px": 30.0}).decision == "verify"


def test_slow_camera_abstains_and_says_why():
    """Measured on the dev laptop: a dim room drove the webcam to ~10 fps, below Nyquist for 8 Hz."""
    f = features()
    f = replace(f, measurement=replace(f.measurement, capture_fps=10.0))
    v = decide(f, CALIBRATED)
    assert v.decision == "abstain" and "capture_fps" in v.reason and "light" in v.reason


def test_no_features_abstains():
    assert decide(None).decision == "abstain"


def test_lamp_abstains_before_l1_could_reject():
    """A lamp on the face lowers its relative response; that must be abstain, not reject."""
    v = decide(features(face=0.3, lightness=1.4), CALIBRATED)
    assert v.decision == "abstain"


def test_calibrate_from_genuine_sessions_only():
    genuine = [features(face=0.84, gain=1.0, contrast=0.45, lightness=1.03),
               features(face=0.88, gain=1.2, contrast=0.50, lightness=1.06),
               features(face=0.80, gain=0.9, contrast=0.42, lightness=1.05)]
    poses = [{"yaw": 0.01, "yaw_iqr": 0.02}, {"yaw": -0.05, "yaw_iqr": 0.01}, {"yaw": 0.03, "yaw_iqr": 0.02}]
    th = calibrate(genuine, poses=poses)

    assert not th.provisional and th.n_genuine == 3
    assert 0.0 < th.cheek_contrast_min < 0.42
    assert th.max_abs_yaw == pytest.approx(0.05 + 0.02 + 0.03)
    assert th.face_neck_lightness_max == pytest.approx(1.09)
    assert th.gain_range == (pytest.approx(0.36), pytest.approx(2.4))  # 0.4 x min, 2 x max
    assert all(decide(f, th).decision == "verify" for f in genuine)
    assert decide(features(contrast=0.1), th).decision == "reject"


def test_l3_bound_includes_each_sessions_own_noise():
    """Measured G1-G3 (0.589 +/- 0.029, 0.573 +/- 0.036, 0.544 +/- 0.027): the bound must allow for
    within-session noise, not only the (tiny, n = 3) spread between sessions."""
    genuine = [replace(features(contrast=c), cheek_contrast_se=se)
               for c, se in ((0.589, 0.029), (0.573, 0.036), (0.544, 0.027))]
    th = calibrate(genuine)
    assert th.cheek_contrast_min == pytest.approx(0.453, abs=0.003)
    between_only = 0.5687 - 3 * 0.0228
    assert th.cheek_contrast_min < between_only


def test_calibrate_with_few_sessions_stays_provisional():
    th = calibrate([features()])
    assert th.provisional
    assert th.cheek_contrast_min == pytest.approx(0.8 * 0.45)
    assert th.max_abs_yaw is None


def test_thresholds_round_trip_and_missing_file(tmp_path):
    th = replace(CALIBRATED, gain_range=(0.5, 2.0))
    th.save(tmp_path / "t.json")
    assert load_thresholds(tmp_path / "t.json") == th
    assert load_thresholds(tmp_path / "missing.json") == Thresholds()
