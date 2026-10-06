"""deepcheck.py: neck halves and the neck-referenced lean, on SIMULATED series."""

import numpy as np
import pytest

from lumen.deepcheck import NECK_HALVES, lean_measures, neck_half_masks
from lumen.probe import Probe
from lumen.synthetic import LANDMARKS, SIZE


def test_neck_halves_sit_left_and_right_of_the_face_centre():
    masks = neck_half_masks(LANDMARKS, SIZE)
    assert set(masks) == set(NECK_HALVES)
    centre_x = LANDMARKS.points[:2, 0].mean()
    xs = {n: np.nonzero(m)[1].mean() for n, m in masks.items()}
    assert xs["neck.l"] < centre_x < xs["neck.r"]
    assert not np.any((masks["neck.l"] > 0) & (masks["neck.r"] > 0))


def _series(shares: dict, seconds=40.0, fs=30.0, noise=0.02, seed=1):
    """Each region's chroma = its (left, right) share of the two probe halves' light, plus noise."""
    probe = Probe("deepcheck-test", duration_s=seconds)
    t = np.arange(0, seconds, 1 / fs)
    p = np.array([probe.chroma_at(x) for x in t])
    rng = np.random.default_rng(seed)
    regions = {}
    for name, (wl, wr) in shares.items():
        x = 0.05 * (wl * p[:, :2] + wr * p[:, 2:]) + noise * rng.standard_normal((len(t), 2))
        regions[name] = (t, x)
    return t, p, regions


def test_lean_ratio_follows_the_geometry():
    real = {"face": (0.5, 0.5), "neck": (0.5, 0.5),
            "face.cheek_l": (0.3, 0.7), "face.cheek_r": (0.7, 0.3),
            "neck.l": (0.4, 0.6), "neck.r": (0.6, 0.4)}
    t, p, regions = _series(real)
    m = lean_measures(t, p, regions, max_lag_s=0.2)
    # lateral index = (wl - wr) / (wl + wr): cheeks +-0.4, neck halves +-0.2
    assert m.cheek_contrast == pytest.approx(0.8, abs=0.05)
    assert m.neck_contrast == pytest.approx(0.4, abs=0.05)
    assert m.lean_ratio == pytest.approx(2.0, abs=0.25)
    assert m.lean_ratio_se is not None and m.lean_ratio_se < 0.3


def test_a_weakened_face_lean_lowers_the_ratio_but_not_the_neck():
    swap = {"face": (0.5, 0.5), "neck": (0.5, 0.5),
            "face.cheek_l": (0.36, 0.64), "face.cheek_r": (0.64, 0.36),  # 70 % of the lean kept
            "neck.l": (0.4, 0.6), "neck.r": (0.6, 0.4)}
    t, p, regions = _series(swap)
    m = lean_measures(t, p, regions, max_lag_s=0.2)
    assert m.neck_contrast == pytest.approx(0.4, abs=0.05)
    assert m.lean_ratio == pytest.approx(1.4, abs=0.25)


# --- 3D relief and the two-sign judgement --------------------------------------------------------

from lumen.deepcheck import (DeepMeasures, DeepThresholds, SUBREGIONS, calibrate_deep, deep_measures,  # noqa: E402
                             judge, relief)
from lumen.features import segment_spectra  # noqa: E402


def _patch_segs(gains, noise, seed=2):
    """Face patches responding to a split probe with the given gains (+ noise): name -> SegmentSpectra."""
    t, p, _ = _series({}, seed=seed)
    rng = np.random.default_rng(seed)
    segs = {}
    for name, g in zip(SUBREGIONS, gains):
        x = 0.05 * g * (0.5 * p[:, :2] + 0.5 * p[:, 2:]) + noise * rng.standard_normal((len(t), 2))
        segs[name] = segment_spectra(p, x, 30.0, 60)
    return segs


def test_relief_is_high_for_uneven_patches_and_low_for_even_ones():
    real = relief(_patch_segs([0.8, 2.2, 1.8, 1.3, 2.4], noise=0.02))
    flat = relief(_patch_segs([1.5] * 5, noise=0.02))
    assert real > 0.3
    assert flat < 0.05


def test_relief_is_not_inflated_by_noise():
    """Noise makes a plain gain estimate wobble from patch to patch; the split-half estimate does not."""
    noisy_flat = relief(_patch_segs([1.5] * 5, noise=0.2))
    assert noisy_flat < 0.12


def _m(contrast=0.48, rel=0.40, neck=0.6, floor=0.045):
    return DeepMeasures("s", contrast, 0.03, rel, 0.03, neck, floor)


TH = DeepThresholds(contrast_min=0.35, relief_min=0.24, contrast_sign=1, k=2.0, n_genuine=3, sessions=())


@pytest.mark.parametrize("m, decision", [
    (_m(), "real"),
    (_m(contrast=0.33), "real"),            # one sign low is not enough
    (_m(rel=0.15), "real"),
    (_m(contrast=0.33, rel=0.15), "swap"),  # both low: the measured FaceFusion case
    (_m(neck=0.10), "abstain"),             # probe too weak to judge
    (None, "abstain"),
])
def test_judge_needs_both_signs_low(m, decision):
    assert judge(m, TH).decision == decision


def test_calibration_uses_genuine_only_and_learns_a_mirrored_sign():
    genuine = [_m(contrast=-c, rel=r) for c, r in ((0.50, 0.48), (0.53, 0.41), (0.42, 0.45))]
    th = calibrate_deep(genuine)
    assert th.contrast_sign == -1 and th.n_genuine == 3
    assert 0.0 < th.contrast_min < 0.42 and 0.0 < th.relief_min < 0.41
    assert judge(_m(contrast=-0.48, rel=0.44), th).decision == "real"
    with pytest.raises(ValueError):
        calibrate_deep(genuine[:2])


def test_deep_measures_on_a_simulated_session(tmp_path):
    from conftest import FakeDisplay
    from lumen.decide import Thresholds
    from lumen.record import load_record
    from lumen.session import Session, SessionConfig

    cfg = SessionConfig(source="synthetic:genuine", duration_s=12.0, warmup_s=1.0, segment_s=1.0,
                        out_dir=str(tmp_path / "s"), key_path=str(tmp_path / "k.pem"),
                        thresholds_path=str(tmp_path / "t.json"))
    r = Session(cfg, display_factory=lambda c: FakeDisplay(), thresholds=Thresholds(min_duration_s=3.0)).run()
    m = deep_measures(load_record(r.record_path))
    assert m is not None and m.simulated
    assert m.cheek_contrast > 0.2 and m.relief is not None


def test_live_runs_a_session_then_the_deep_check(tmp_path, monkeypatch, capsys):
    """The live command on a SIMULATED source, with no window (a fake display) and no camera."""
    from conftest import FakeDisplay
    import lumen.session
    from lumen.deepcheck import latest_sessions, main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lumen.session, "default_display", lambda cfg: FakeDisplay())
    assert main(["live", "--source", "synthetic:genuine", "--duration", "24", "--label", "deep-test"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT:" in out and "DEEP CHECK" in out and "SIMULATED" in out
    assert "not calibrated yet" in out
    # simulated sessions never count as genuine calibration data
    assert latest_sessions(3, "deep-test") == []
    folders = list((tmp_path / "data" / "sessions").iterdir())
    assert len(folders) == 1
    assert main(["check", str(folders[0])]) == 0
