"""Synthetic-signal tests for features.py: signals with known coherence, gain and lean.

For y = a x + b n, with x and n independent and unit-variance, the true magnitude-squared
coherence is a^2 / (a^2 + b^2) at every frequency. With several inputs and a mixing matrix M,
the multiple coherence of output c is |M_c|^2 / (|M_c|^2 + sigma^2).
"""

import numpy as np
import pytest
from scipy import signal

from lumen.features import (
    analyse,
    cheek_contrast,
    estimate_lag,
    lateral_index,
    measure,
    region_response,
    spectrum,
)
from lumen.probe import EmissionLog, Probe

FS = 30.0
NPERSEG = 60  # 2 s segments
BAND = (2.0, 8.0)


def rotation(theta: float) -> np.ndarray:
    return np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])


def band_coherence(probe, region) -> float:
    return region_response(spectrum(probe, region, FS, NPERSEG), BAND).coherence


# --- L1: coherence estimator against known ground truth --------------------------------------

@pytest.mark.parametrize("target", [0.1, 0.3, 0.5, 0.7, 0.9, 0.99])
def test_recovers_known_single_channel_coherence(target):
    rng = np.random.default_rng(int(target * 1000))
    n = int(120 * FS)
    x, noise = rng.standard_normal(n), rng.standard_normal(n)
    y = np.sqrt(target) * x + np.sqrt(1 - target) * noise
    assert band_coherence(x, y) == pytest.approx(target, abs=0.05)


def test_single_input_equals_scipy_coherence():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(1800)
    y = 0.6 * x + rng.standard_normal(1800)
    ours = spectrum(x, y, FS, NPERSEG)
    f, ref = signal.coherence(x, y, fs=FS, nperseg=NPERSEG, detrend="linear")
    np.testing.assert_allclose(ours.f, f)
    np.testing.assert_allclose(ours.coherence[:, 0], ref, rtol=1e-10, atol=1e-12)


@pytest.mark.parametrize("theta", [0.0, 0.7, 2.0])
def test_recovers_known_multiple_coherence_through_unknown_mixing(theta):
    """Camera chroma axes are a rotated, rescaled mix of the display's: coherence must not care."""
    rng = np.random.default_rng(2)
    n = int(120 * FS)
    x = rng.standard_normal((n, 2))
    mix = rotation(theta) @ np.diag([1.0, 0.5])  # rows have different norms
    sigma = 0.6
    y = x @ mix.T + sigma * rng.standard_normal((n, 2))

    resp = region_response(spectrum(x, y, FS, NPERSEG), BAND)
    row_power = (mix**2).sum(axis=1)
    np.testing.assert_allclose(resp.coherence_per_channel, row_power / (row_power + sigma**2), atol=0.05)
    assert resp.coherence == pytest.approx(row_power.sum() / (row_power.sum() + 2 * sigma**2), abs=0.05)


# --- Noise floor ------------------------------------------------------------------------------

@pytest.mark.parametrize("n_inputs", [1, 2, 4])
def test_pure_noise_sits_at_noise_floor(n_inputs):
    """No response at all: one realisation stays near p/K, and the mean over many is p/K."""
    rng = np.random.default_rng(3)
    n = int(60 * FS)
    values = []
    for _ in range(200):
        spec = spectrum(rng.standard_normal((n, n_inputs)), rng.standard_normal((n, 2)), FS, NPERSEG)
        values.append(region_response(spec, BAND).coherence)
    floor = spec.noise_floor
    assert spec.n_segments == 59
    assert floor == pytest.approx(n_inputs / 59)
    # 50% Welch overlap makes segments slightly dependent, so the true null mean sits a little
    # above p/K. The floor is "roughly p/K" per PROJECT.md §8.
    assert 0.9 * floor < np.mean(values) < 1.2 * floor
    assert np.percentile(values, 99) < 2.5 * floor


def test_noise_floor_falls_with_more_segments():
    rng = np.random.default_rng(4)
    short = spectrum(rng.standard_normal((900, 2)), rng.standard_normal((900, 2)), FS, NPERSEG)
    long = spectrum(rng.standard_normal((3600, 2)), rng.standard_normal((3600, 2)), FS, NPERSEG)
    assert long.noise_floor < short.noise_floor / 3


# --- L2: gain -------------------------------------------------------------------------------

@pytest.mark.parametrize("true_ratio", [1.0, 0.6, 0.2])
def test_recovers_differential_gain_despite_different_mixing(true_ratio):
    rng = np.random.default_rng(5)
    n = int(60 * FS)
    x = rng.standard_normal((n, 2))
    neck = 2.0 * x @ rotation(0.3).T + 0.3 * rng.standard_normal((n, 2))
    face = 2.0 * true_ratio * x @ rotation(1.9).T + 0.3 * rng.standard_normal((n, 2))
    g_face = region_response(spectrum(x, face, FS, NPERSEG), BAND).gain
    g_neck = region_response(spectrum(x, neck, FS, NPERSEG), BAND).gain
    assert g_neck == pytest.approx(2.0 * np.sqrt(2), rel=0.05)  # |2R|_F
    assert g_face / g_neck == pytest.approx(true_ratio, rel=0.08)


def test_gain_is_unbiased_by_output_noise():
    rng = np.random.default_rng(6)
    n = int(120 * FS)
    x = rng.standard_normal((n, 2))
    clean = region_response(spectrum(x, 1.5 * x, FS, NPERSEG), BAND).gain
    noisy = region_response(spectrum(x, 1.5 * x + 1.5 * rng.standard_normal((n, 2)), FS, NPERSEG), BAND).gain
    assert noisy == pytest.approx(clean, rel=0.08)


# --- L3: lateral index ---------------------------------------------------------------------

@pytest.mark.parametrize("w_left, w_right", [(0.5, 0.5), (0.7, 0.3), (0.2, 0.8)])
def test_lateral_index_recovers_known_weights(w_left, w_right):
    rng = np.random.default_rng(int(w_left * 100))
    p = Probe("split-nonce", duration_s=60.0, amplitude=16.0).chroma_at(np.arange(int(60 * FS)) / FS)
    r = (w_left * p[:, :2] + w_right * p[:, 2:]) @ rotation(0.4).T * 0.1
    r += 0.1 * rng.standard_normal(r.shape)
    li = lateral_index(spectrum(p, r, FS, 60))
    assert li == pytest.approx((w_left - w_right) / (w_left + w_right), abs=0.05)


def test_lateral_index_is_none_for_single_probe():
    rng = np.random.default_rng(0)
    assert lateral_index(spectrum(rng.standard_normal((900, 2)), rng.standard_normal((900, 2)), FS, 60)) is None


def test_cheek_contrast_needs_both_cheeks():
    assert cheek_contrast({"face.cheek_r": 0.3, "face.cheek_l": -0.2}) == pytest.approx(0.5)
    assert cheek_contrast({"face.cheek_r": 0.3}) is None


# --- Delay -----------------------------------------------------------------------------------

def test_estimate_lag_recovers_delay_regardless_of_mixing_and_sign():
    rng = np.random.default_rng(7)
    x = rng.standard_normal((2000, 2))
    d = 11
    y = -(x[:-d] @ rotation(2.5).T) + 0.5 * rng.standard_normal((2000 - d, 2))
    assert estimate_lag(x[: len(y)], np.vstack([np.zeros((d, 2)), y])[: len(y)], FS, 1.0) == d / FS


def test_estimate_lag_survives_slow_drift():
    """Head motion / auto-exposure drift below 2 Hz must not bury the lag peak.

    Strong enough drift leaks into 2-8 Hz itself, which no filter can remove, so a couple of
    misses in 20 draws are allowed.
    """
    n, d = 900, 3  # 30 s at 30 Hz, true delay 100 ms
    p = Probe("drift", layout="single", duration_s=31.0).chroma_at(np.arange(n) / FS)
    delayed = np.vstack([np.zeros((d, 2)), p[: n - d]])  # r[t] responds to p[t - d]
    hits = 0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        drift = np.cumsum(rng.standard_normal((n, 2)), axis=0) * 0.6  # random walk, mostly < 1 Hz
        r = 0.08 * delayed @ rotation(0.5).T + drift + 0.3 * rng.standard_normal((n, 2))
        hits += abs(estimate_lag(p, r, FS, 1.0) - d / FS) <= 1.01 / FS
    assert hits >= 18


# --- End to end: timestamps, probe log, camera ------------------------------------------------

def simulate_session(*, face_gain, neck_gain, delay_s, noise, seed, layout="single",
                     response_probe: Probe | None = None, duration=60.0, weights=None):
    """A 60 Hz emission log and a jittery ~30 fps camera with dropped frames, on one clock.

    Each region's chroma = gain * mix @ drive(t_capture - delay) + noise, where drive is the probe
    chroma, or for a split probe the left/right halves mixed by `weights[region]`. The camera
    clock starts at an arbitrary offset; nothing is aligned by index.
    """
    rng = np.random.default_rng(seed)
    probe = Probe(f"session-{seed}", layout=layout, duration_s=duration, amplitude=16.0)
    t0 = 500.0
    log = EmissionLog(probe, t0=t0)
    for t in t0 + np.arange(int(duration * 60)) / 60 + rng.uniform(0, 0.002, int(duration * 60)):
        log.emit(t)

    source = response_probe or probe  # a replay responds to some other session's probe
    cam_t = t0 + delay_s + 0.37 + np.arange(int((duration - delay_s - 1) * 30)) / 30
    cam_t = cam_t + rng.normal(0, 0.003, cam_t.size)
    cam_t = np.sort(cam_t[rng.random(cam_t.size) > 0.05])  # drop 5% of frames
    lit = source.chroma_at(cam_t - delay_s - t0)

    def region(name, gain, theta):
        drive = lit
        if lit.shape[1] == 4:
            w_left, w_right = (weights or {}).get(name, (0.5, 0.5))
            drive = w_left * lit[:, :2] + w_right * lit[:, 2:]
        return 50 + gain * drive @ rotation(theta).T + noise * rng.standard_normal((cam_t.size, 2))

    return log, cam_t, region


def test_measure_genuine_session_with_delay_jitter_and_drops():
    log, cam_t, region = simulate_session(face_gain=0.3, neck_gain=0.2, delay_s=0.35, noise=0.3, seed=10)
    m = measure(log.t, log.chroma, cam_t, region("face", 0.3, 0.4), cam_t, region("neck", 0.2, 1.1), fs=FS)

    assert m.lag_s == pytest.approx(0.35, abs=1.5 / FS)
    assert m.face.coherence > 0.9
    assert m.neck.coherence > 0.8
    assert m.differential_gain == pytest.approx(0.3 / 0.2, rel=0.1)
    assert m.n_segments > 50
    assert m.band_hz == BAND
    assert m.max_gap_s > 1 / 30  # dropped frames are reported, not hidden


def test_measure_face_swap_face_falls_to_noise_floor_while_neck_follows():
    """An unlit swap regenerates the face without the probe; the neck keeps responding."""
    log, cam_t, region = simulate_session(face_gain=0.0, neck_gain=0.2, delay_s=0.35, noise=0.3, seed=11)
    m = measure(log.t, log.chroma, cam_t, region("face", 0.0, 0.4), cam_t, region("neck", 0.2, 1.1), fs=FS)
    assert m.neck.coherence > 0.8
    assert m.face.coherence < 2.5 * m.noise_floor
    assert m.differential_gain < 0.15


def test_measure_replay_of_another_session_sits_at_noise_floor():
    """A recording responds to a previous session's probe, not today's one-time sequence."""
    old = Probe("session-old", layout="single", duration_s=60.0, amplitude=16.0)
    log, cam_t, region = simulate_session(face_gain=0.3, neck_gain=0.2, delay_s=0.35, noise=0.3, seed=12,
                                          response_probe=old)
    m = measure(log.t, log.chroma, cam_t, region("face", 0.3, 0.4), cam_t, region("neck", 0.2, 1.1), fs=FS)
    assert m.face.coherence < 2.5 * m.noise_floor
    assert m.neck.coherence < 2.5 * m.noise_floor


def test_uncompensated_delay_would_masquerade_as_low_coherence():
    """Why lag estimation exists: a 0.5 s pipeline delay alone wrecks a genuine face's score."""
    log, cam_t, region = simulate_session(face_gain=0.3, neck_gain=0.2, delay_s=0.5, noise=0.1, seed=14)
    face, neck = region("face", 0.3, 0.4), region("neck", 0.2, 1.1)
    compensated = measure(log.t, log.chroma, cam_t, face, cam_t, neck, fs=FS, max_lag_s=1.0)
    naive = measure(log.t, log.chroma, cam_t, face, cam_t, neck, fs=FS, max_lag_s=0.0)
    assert compensated.face.coherence > 0.95
    assert naive.face.coherence < 0.6


def test_analyse_split_session_reports_cheek_contrast_and_floor():
    """Two-sided probe: cheeks lean to opposite halves; the floor uses p = 4 inputs."""
    weights = {"face.cheek_r": (0.65, 0.35), "face.cheek_l": (0.35, 0.65)}
    log, cam_t, region = simulate_session(face_gain=0.3, neck_gain=0.2, delay_s=0.2, noise=0.2, seed=20,
                                          layout="split", weights=weights)
    regions = {name: (cam_t, region(name, 0.3 if name != "neck" else 0.2, 0.4))
               for name in ("face", "neck", "face.cheek_r", "face.cheek_l")}
    f = analyse(log.t, log.chroma, regions, fs=FS, face_neck_lightness=1.1)

    assert f.measurement.noise_floor == pytest.approx(4 / f.measurement.n_segments)
    assert f.lateral["face.cheek_r"] == pytest.approx(0.3, abs=0.05)
    assert f.lateral["face.cheek_l"] == pytest.approx(-0.3, abs=0.05)
    assert f.cheek_contrast == pytest.approx(0.6, abs=0.08)
    assert abs(f.lateral["neck"]) < 0.05
    assert f.face_neck_lightness == 1.1
    assert set(f.spatial_field) == {"cheek_r", "cheek_l"}


def test_analyse_requires_face_and_neck():
    with pytest.raises(ValueError, match="neck"):
        analyse([0, 1], [[0, 0], [1, 1]], {"face": ([0, 1], [[0, 0], [1, 1]])})
