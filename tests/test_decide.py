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
    assert th.gain_range == (pytest.approx(0.63), pytest.approx(1.68))
    assert all(decide(f, th).decision == "verify" for f in genuine)
    assert decide(features(contrast=0.1), th).decision == "reject"


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
