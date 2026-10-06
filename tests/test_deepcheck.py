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
