import numpy as np
import pytest

from lumen.colour import XYZ_TO_LINEAR_SRGB, lab_to_linear_srgb
from lumen.probe import EmissionLog, Probe, lab_to_chroma, new_nonce

DURATION = 60.0
FS_DENSE = 240.0


def dense_chroma(probe: Probe) -> np.ndarray:
    """One exact period of the probe, sampled at FS_DENSE."""
    return probe.chroma_at(np.arange(int(DURATION * FS_DENSE)) / FS_DENSE)


@pytest.fixture(scope="module", params=["single", "split"])
def probe(request) -> Probe:
    return Probe("test-nonce-1", layout=request.param, duration_s=DURATION, amplitude=16.0)


def test_same_nonce_gives_identical_sequence(probe):
    other = Probe("test-nonce-1", layout=probe.layout, duration_s=DURATION, amplitude=16.0)
    t = np.linspace(0, DURATION, 1000)
    np.testing.assert_array_equal(probe.chroma_at(t), other.chroma_at(t))


def test_str_and_bytes_nonce_are_equivalent(probe):
    other = Probe(b"test-nonce-1", layout=probe.layout, duration_s=DURATION, amplitude=16.0)
    for a, b in zip(probe.sequences, other.sequences):
        np.testing.assert_array_equal(a.phases, b.phases)


def test_channel_count_follows_layout(probe):
    x = probe.chroma_at(np.linspace(0, DURATION, 10))
    assert x.shape == (10, probe.n_channels)
    assert probe.n_channels == (4 if probe.layout == "split" else 2)
    assert probe.lab_at(1.0).shape == ((2, 3) if probe.layout == "split" else (3,))


def test_all_channels_are_mutually_uncorrelated(probe):
    c = np.corrcoef(dense_chroma(probe).T)
    off_diagonal = c[~np.eye(len(c), dtype=bool)]
    assert np.all(np.abs(off_diagonal) < 0.2)


def test_different_nonces_are_uncorrelated(probe):
    a = dense_chroma(probe)
    b = dense_chroma(Probe("test-nonce-2", layout=probe.layout, duration_s=DURATION, amplitude=16.0))
    for i in range(a.shape[1]):
        for j in range(b.shape[1]):
            assert abs(np.corrcoef(a[:, i], b[:, j])[0, 1]) < 0.2


def test_split_halves_differ_from_the_single_sequence():
    t = np.arange(0, DURATION, 1 / 120)
    single = Probe("n", layout="single", duration_s=DURATION, amplitude=16.0).chroma_at(t)
    split = Probe("n", layout="split", duration_s=DURATION, amplitude=16.0).chroma_at(t)
    assert abs(np.corrcoef(single[:, 0], split[:, 0])[0, 1]) < 0.2


def test_power_is_confined_to_band(probe):
    x = dense_chroma(probe)
    f = np.fft.rfftfreq(len(x), 1 / FS_DENSE)
    power = np.abs(np.fft.rfft(x, axis=0)) ** 2
    in_band = (f >= 2.0) & (f <= 8.0)
    assert np.all(power[in_band].sum(axis=0) / power.sum(axis=0) > 0.9999)


def test_in_band_spectrum_is_flat(probe):
    x = dense_chroma(probe)
    f = np.fft.rfftfreq(len(x), 1 / FS_DENSE)
    mag = np.abs(np.fft.rfft(x, axis=0))[(f >= 2.0) & (f <= 8.0)]
    np.testing.assert_allclose(mag / mag.mean(axis=0), 1.0, rtol=1e-6)


def test_peak_matches_amplitude(probe):
    peak = np.abs(dense_chroma(probe)).max(axis=0)
    assert np.all(peak <= probe.amplitude * 1.01)
    assert np.all(peak >= probe.amplitude * 0.98)


def test_lightness_and_luminance_are_constant(probe):
    lab = probe.lab_at(np.linspace(0, DURATION, 5000))
    assert np.all(lab[..., 0] == probe.lightness)
    # Y from linear sRGB via the exact inverse of the matrix used, so only float error remains.
    luminance = lab_to_linear_srgb(lab) @ np.linalg.inv(XYZ_TO_LINEAR_SRGB)[1]
    assert np.std(luminance) / np.mean(luminance) < 1e-9
    assert np.all(np.std(lab[..., 1:], axis=0) > 0.5)


def test_srgb_is_in_gamut(probe):
    rgb = probe.srgb_at(np.linspace(0, DURATION, 5000))
    assert rgb.min() >= 0.0 and rgb.max() <= 1.0


def test_out_of_gamut_configuration_is_refused():
    with pytest.raises(ValueError, match="gamut"):
        Probe("x", duration_s=10, amplitude=80.0)


def test_unknown_layout_is_refused():
    with pytest.raises(ValueError, match="layout"):
        Probe("x", layout="quad")


@pytest.mark.parametrize("t", [-0.01, DURATION + 0.01])
def test_probe_does_not_wrap(probe, t):
    with pytest.raises(ValueError):
        probe.chroma_at(t)


def test_new_nonce_is_unique_128_bit_hex():
    nonces = {new_nonce() for _ in range(100)}
    assert len(nonces) == 100
    assert all(len(n) == 32 and int(n, 16) >= 0 for n in nonces)


def test_params_round_trip(probe):
    again = Probe.from_params("test-nonce-1", probe.params())
    t = np.linspace(0, DURATION, 50)
    np.testing.assert_array_equal(again.chroma_at(t), probe.chroma_at(t))


def test_phases_are_a_pure_function_of_sha256():
    """The sequence must be reproducible from the nonce alone, outside this codebase."""
    import hashlib

    seed = b"golden|left"
    digest = hashlib.sha256(b"lumen/probe/v1" + len(seed).to_bytes(4, "big") + seed
                            + (0).to_bytes(8, "big")).digest()
    first = int.from_bytes(digest[:8], "big") / 2**64
    p = Probe("golden", layout="split", duration_s=10, amplitude=24)
    assert p.sequences[0].phases[0, 0] == pytest.approx(2 * np.pi * first, abs=1e-15)


def test_emission_log_records_each_frame(probe, tmp_path):
    t0 = 1000.0
    rng = np.random.default_rng(0)
    clock = t0 + np.cumsum(rng.uniform(0.012, 0.022, 300))  # jittery ~60 Hz presentation
    log = EmissionLog(probe, t0=t0)
    rgb = np.array([log.emit(t) for t in clock])

    assert len(log) == 300
    np.testing.assert_array_equal(log.t, clock)
    np.testing.assert_allclose(log.lab, probe.lab_at(clock - t0))
    np.testing.assert_allclose(log.chroma, probe.chroma_at(clock - t0))
    np.testing.assert_allclose(lab_to_chroma(log.lab), log.chroma)
    np.testing.assert_allclose(rgb, probe.srgb_at(clock - t0))

    path = tmp_path / "emitted.csv"
    log.save_csv(path)
    loaded = EmissionLog.load_csv(path)
    np.testing.assert_array_equal(loaded["frame"], np.arange(300))
    np.testing.assert_array_equal(loaded["t"], clock)
    first = log.lab if probe.layout == "single" else log.lab[:, 0]
    np.testing.assert_array_equal(np.column_stack([loaded["L"], loaded["a"], loaded["b"]]), first)
    if probe.layout == "split":
        np.testing.assert_array_equal(loaded["b_r"], log.lab[:, 1, 2])


def test_emission_log_rejects_non_increasing_time(probe):
    log = EmissionLog(probe, t0=0.0)
    log.emit(1.0)
    with pytest.raises(ValueError):
        log.emit(1.0)
