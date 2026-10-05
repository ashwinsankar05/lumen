"""Per-region chroma series -> coherence, differential gain and lateral response.

Layers from PROJECT.md §3: L1 (coherence, face relative to neck), L2 (face/neck gain and
background distance falloff), L3 (lateral response to a two-sided probe). Inputs are timestamped
series; alignment is by timestamp, never by frame index (§8).

Model. The probe drives p chroma channels (a*, b* per screen half: p = 2 single, 4 split). Each
region returns C chroma channels (its mean a*, b*). The camera's chroma axes are not the
display's, so each returned channel is an unknown linear mix of all probe channels plus noise:

    R(f) = H(f) P(f) + N(f),    H is C x p

Coherence is therefore *multiple* coherence: the fraction of a returned channel's power at each
frequency that the probe channels explain together. For p = 1 it is exactly ordinary
magnitude-squared coherence (scipy.signal.coherence). With no response its expected value is
about p / K for K Welch segments; that is the noise floor, reported with every result.

Gain is the Frobenius norm |H(f)|_F, which does not depend on how the camera's chroma axes are
rotated relative to the display's. Differential gain is face gain / neck gain.

Lateral index (split probe). The columns of H that belong to the left half and to the right half
give a gain per half; (g_left - g_right) / (g_left + g_right) says which half of the screen a
region leans towards. Cheek contrast = index(image-right cheek) - index(image-left cheek): the
subject's left cheek (image right, camera not mirrored) faces the screen's left half, so a real
face gives a positive contrast.

Delay. The response returns after a pipeline delay (display, camera, codec, network). Welch
segments are a couple of seconds long, so an uncompensated delay biases coherence and gain down.
The delay is estimated once from the neck, the reference channel, and applied to every region.
The face is never optimised over lag, so its noise floor stays honest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import signal

PROBE_BAND_HZ = (2.0, 8.0)
SUBREGION_PREFIX = "face."
CHEEKS = ("face.cheek_r", "face.cheek_l")  # image-right, image-left


@dataclass(frozen=True)
class Spectrum:
    """Welch estimates of how one region responds to the probe, per frequency."""

    f: np.ndarray          # (F,) Hz
    coherence: np.ndarray  # (F, C) multiple coherence of each returned channel on all probe channels
    combined: np.ndarray   # (F,) fraction of total returned power explained by the probe
    gain: np.ndarray       # (F,) |H(f)|_F, region chroma per unit probe chroma
    transfer: np.ndarray   # (F, C, p) H(f)
    n_segments: int
    n_inputs: int

    @property
    def noise_floor(self) -> float:
        """Approximate expected coherence with no response: p / K."""
        return self.n_inputs / self.n_segments


@dataclass(frozen=True)
class RegionResponse:
    coherence: float                          # band mean of Spectrum.combined
    coherence_per_channel: tuple[float, ...]  # band mean of Spectrum.coherence, per channel
    gain: float                               # band mean of Spectrum.gain


@dataclass(frozen=True)
class Measurement:
    face: RegionResponse
    neck: RegionResponse
    differential_gain: float  # face.gain / neck.gain
    lag_s: float              # pipeline delay estimated from the neck
    band_hz: tuple[float, float]
    fs: float
    duration_s: float         # length of the aligned series that was analysed
    n_segments: int
    noise_floor: float
    max_gap_s: float          # largest gap between timestamps in any input; gaps are interpolated over
    capture_fps: float = 30.0  # achieved frame rate of the face series (median frame interval)


@dataclass(frozen=True)
class Features:
    """Everything the decision uses, for one session."""

    measurement: Measurement
    # Coherence of the face with the neck (neck as input). High while neither follows today's
    # probe means both respond to some other colour signal: a replay of an earlier session.
    face_neck_coherence: float
    # L2 distance falloff: background gain / neck gain. Light from the screen falls off steeply
    # with distance, so a far wall responds far less than the neck. None without a background.
    background_gain_ratio: float | None
    # Diagnostic only: gain of each face sub-region / mean sub-region gain, and its spread. It
    # depends on room lighting, so decide.py does not enforce it (PROJECT.md §4).
    spatial_field: dict[str, float] = field(default_factory=dict)
    spatial_cv: float | None = None
    # Split probe only: per region, (left gain - right gain) / (left + right).
    lateral: dict[str, float] = field(default_factory=dict)
    # L3: lateral index of the image-right cheek minus the image-left cheek (None if single),
    # and its within-session jackknife standard error.
    cheek_contrast: float | None = None
    cheek_contrast_se: float | None = None
    # Mean L* of the face / mean L* of the neck. A lamp near the subject lights the face more
    # than in any genuine calibration session; decide.py abstains on it.
    face_neck_lightness: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# --- spectral estimates ----------------------------------------------------------------------

def _as_2d(x) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return x[:, None] if x.ndim == 1 else x


def band_mean(f: np.ndarray, values: np.ndarray, band_hz: tuple[float, float] = PROBE_BAND_HZ):
    """Mean of `values` over frequency bins inside the band (inclusive)."""
    mask = (f >= band_hz[0]) & (f <= band_hz[1])
    if not mask.any():
        raise ValueError(f"no frequency bins in band {band_hz}")
    return values[mask].mean(axis=0)


@dataclass(frozen=True)
class SegmentSpectra:
    """Per-segment cross-spectra (Hann window, linear detrend, 50% overlap), one row per segment.

    Their mean over segments is the Welch estimate; keeping them separate lets a jackknife leave
    out a block of segments without re-analysing anything. Scaling constants are omitted: every
    quantity derived from them (coherence, transfer, gain) is a ratio in which they cancel.
    """

    f: np.ndarray    # (F,) Hz
    Spp: np.ndarray  # (K, F, p, p)  P P^H
    Srp: np.ndarray  # (K, F, C, p)  R P^H
    Srr: np.ndarray  # (K, F, C)     |R|^2

    @property
    def n_segments(self) -> int:
        return self.Spp.shape[0]

    def spectrum(self, keep: np.ndarray | None = None) -> Spectrum:
        """Welch estimate over all segments, or only those where `keep` (bool, (K,)) is True."""
        k = slice(None) if keep is None else keep
        return _spectrum(self.f, self.Spp[k].sum(axis=0), self.Srp[k].sum(axis=0), self.Srr[k].sum(axis=0),
                         n_segments=self.n_segments if keep is None else int(np.sum(keep)))


def segment_spectra(probe, region, fs: float, nperseg: int) -> SegmentSpectra:
    """Per-segment spectra of the probe and one region, from aligned uniform series.

    probe: (N,) or (N, p). region: (N,) or (N, C). Both sampled at fs on the same grid.
    """
    P, R = _as_2d(probe), _as_2d(region)
    if len(P) != len(R):
        raise ValueError("probe and region series differ in length")
    if len(P) < nperseg:
        raise ValueError(f"series of {len(P)} samples is shorter than one segment ({nperseg})")
    step = nperseg - nperseg // 2
    idx = np.arange(0, len(P) - nperseg + 1, step)[:, None] + np.arange(nperseg)  # (K, nperseg)
    window = signal.get_window("hann", nperseg)[None, :, None]
    X = np.fft.rfft(signal.detrend(P[idx], axis=1, type="linear") * window, axis=1)  # (K, F, p)
    Y = np.fft.rfft(signal.detrend(R[idx], axis=1, type="linear") * window, axis=1)  # (K, F, C)
    return SegmentSpectra(
        f=np.fft.rfftfreq(nperseg, 1 / fs),
        Spp=X[..., :, None] * X.conj()[..., None, :],
        Srp=Y[..., :, None] * X.conj()[..., None, :],
        Srr=np.abs(Y) ** 2,
    )


def _spectrum(f, Gpp, Grp, Grr, *, n_segments: int) -> Spectrum:
    # Y = H X + N  =>  E[Y X^H] = H E[X X^H], so H = Grp Gpp^-1, solved as Gpp^T H^T = Grp^T.
    H = np.linalg.solve(Gpp.transpose(0, 2, 1), Grp.transpose(0, 2, 1)).transpose(0, 2, 1)
    explained = np.einsum("fcp,fcp->fc", H, Grp.conj()).real  # diag(H Grp^H): probe-explained power
    return Spectrum(
        f=f,
        coherence=explained / Grr,
        combined=explained.sum(axis=1) / Grr.sum(axis=1),
        gain=np.linalg.norm(H, axis=(1, 2)),
        transfer=H,
        n_segments=n_segments,
        n_inputs=Gpp.shape[-1],
    )


def spectrum(probe, region, fs: float, nperseg: int) -> Spectrum:
    """Coherence, gain and transfer of one region against the probe (Welch, all segments)."""
    return segment_spectra(probe, region, fs, nperseg).spectrum()


def jackknife_blocks(n_segments: int, n_blocks: int) -> list[np.ndarray]:
    """Boolean keep-masks, each leaving out one block of consecutive segments."""
    edges = np.linspace(0, n_segments, n_blocks + 1).round().astype(int)
    masks = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        keep = np.ones(n_segments, bool)
        keep[lo:hi] = False
        masks.append(keep)
    return masks


def jackknife_se(values: list[float]) -> float:
    """Delete-one-group jackknife standard error from the leave-one-out estimates."""
    v = np.asarray(values, dtype=float)
    return float(np.sqrt((len(v) - 1) / len(v) * np.sum((v - v.mean()) ** 2)))


def region_response(spec: Spectrum, band_hz: tuple[float, float] = PROBE_BAND_HZ) -> RegionResponse:
    return RegionResponse(
        coherence=float(band_mean(spec.f, spec.combined, band_hz)),
        coherence_per_channel=tuple(float(v) for v in band_mean(spec.f, spec.coherence, band_hz)),
        gain=float(band_mean(spec.f, spec.gain, band_hz)),
    )


def lateral_index(spec: Spectrum, band_hz: tuple[float, float] = PROBE_BAND_HZ) -> float | None:
    """(g_left - g_right) / (g_left + g_right) for a split probe (p = 4), else None.

    g_left and g_right are band-mean Frobenius norms of the transfer from each half's (a*, b*).
    """
    if spec.n_inputs != 4:
        return None
    g_left = float(band_mean(spec.f, np.linalg.norm(spec.transfer[:, :, :2], axis=(1, 2)), band_hz))
    g_right = float(band_mean(spec.f, np.linalg.norm(spec.transfer[:, :, 2:], axis=(1, 2)), band_hz))
    return (g_left - g_right) / (g_left + g_right)


def cheek_contrast(lateral: Mapping[str, float]) -> float | None:
    """Lateral index of the image-right cheek minus the image-left cheek; positive on a real face."""
    right, left = CHEEKS
    if right in lateral and left in lateral:
        return lateral[right] - lateral[left]
    return None


# --- alignment -------------------------------------------------------------------------------

def resample(t, x, grid) -> np.ndarray:
    """Linearly interpolate a timestamped series (N,) or (N, C) onto `grid`. Returns (M, C)."""
    t = np.asarray(t, dtype=float)
    x = _as_2d(x)
    if len(t) != len(x):
        raise ValueError("timestamps and values differ in length")
    if np.any(np.diff(t) <= 0):
        raise ValueError("timestamps must be strictly increasing")
    return np.column_stack([np.interp(grid, t, x[:, c]) for c in range(x.shape[1])])


def estimate_lag(probe, region, fs: float, max_lag_s: float,
                 band_hz: tuple[float, float] = PROBE_BAND_HZ) -> float:
    """Delay in [0, max_lag_s] at which the region best follows the probe, in seconds.

    Both series are on the same uniform grid at zero lag, and both are band-passed to the probe
    band first: real footage carries large slow drift (head motion, auto-exposure) that the probe
    has no power to explain, and left in, it buries the true correlation peak
    (test_estimate_lag_survives_slow_drift). The score sums squared cross-correlation over all channel pairs, so it does not
    depend on the chroma-axis mixing or the sign of the response. Only non-negative lags: a
    response cannot precede the emission.
    """
    sos = signal.butter(4, band_hz, btype="bandpass", fs=fs, output="sos")
    P = signal.sosfiltfilt(sos, _as_2d(probe), axis=0)
    R = signal.sosfiltfilt(sos, _as_2d(region), axis=0)
    n = len(P)
    kmax = min(int(round(max_lag_s * fs)), n // 2)
    scores = [np.sum((R[k:].T @ P[:n - k]) ** 2) / (n - k) ** 2 for k in range(kmax + 1)]
    return int(np.argmax(scores)) / fs


def _grid(fs: float, lag_s: float, probe_t, *region_ts) -> np.ndarray:
    """Uniform grid where every region has data and the probe has data at t - lag."""
    start = max(probe_t[0] + lag_s, *(t[0] for t in region_ts))
    stop = min(probe_t[-1] + lag_s, *(t[-1] for t in region_ts))
    if stop <= start:
        raise ValueError("probe log and region series do not overlap in time")
    return start + np.arange(int(np.floor((stop - start) * fs)) + 1) / fs


@dataclass(frozen=True)
class Aligned:
    """Region series and the probe on one uniform grid on the capture clock."""

    t: np.ndarray                   # (N,) grid times
    probe: np.ndarray               # (N, p) probe chroma emitted lag_s before each grid time
    regions: dict[str, np.ndarray]  # name -> (N, C)
    lag_s: float


def align(probe_t, probe_chroma, regions: Mapping[str, tuple], *, fs: float, max_lag_s: float,
          reference: str = "neck") -> Aligned:
    """Resample everything onto one grid, with the probe delayed by the lag of the reference.

    regions: name -> (timestamps, chroma). The lag is estimated from `reference` only.
    """
    probe_t = np.asarray(probe_t, dtype=float)
    ts = {name: np.asarray(t, dtype=float) for name, (t, _) in regions.items()}
    ref_x = regions[reference][1]
    grid0 = _grid(fs, 0.0, probe_t, ts[reference])
    lag = estimate_lag(resample(probe_t, probe_chroma, grid0), resample(ts[reference], ref_x, grid0),
                       fs, max_lag_s)
    grid = _grid(fs, lag, probe_t, *ts.values())
    return Aligned(
        t=grid,
        probe=resample(probe_t, probe_chroma, grid - lag),
        regions={name: resample(ts[name], x, grid) for name, (_, x) in regions.items()},
        lag_s=lag,
    )


# --- analysis --------------------------------------------------------------------------------

def analyse(
    probe_t, probe_chroma, regions: Mapping[str, tuple],
    *,
    fs: float = 30.0,
    segment_s: float = 2.0,
    band_hz: tuple[float, float] = PROBE_BAND_HZ,
    max_lag_s: float = 1.0,
    face_neck_lightness: float | None = None,
    jackknife_n_blocks: int = 6,
) -> Features:
    """L1, L2 and L3 from one capture. regions: name -> (timestamps, chroma (N, C)).

    Must contain "face" and "neck"; may contain "background" and face sub-regions named
    "face.<part>" (see regions.py). Each region may have its own timestamps, e.g. when the
    background is out of frame for a moment. probe_chroma is the emission log's chroma, so the
    layout (single or split) follows from its width.
    """
    for required in ("face", "neck"):
        if required not in regions:
            raise ValueError(f"region {required!r} is required")
    nperseg = int(round(segment_s * fs))
    a = align(probe_t, probe_chroma, regions, fs=fs, max_lag_s=max_lag_s)
    segs = {name: segment_spectra(a.probe, x, fs, nperseg) for name, x in a.regions.items()}
    specs = {name: s.spectrum() for name, s in segs.items()}
    resp = {name: region_response(s, band_hz) for name, s in specs.items()}
    face, neck = resp["face"], resp["neck"]

    gap = float(max(np.diff(np.asarray(ts, dtype=float)).max()
                    for ts in (probe_t, regions["face"][0], regions["neck"][0])))
    m = Measurement(
        face=face,
        neck=neck,
        differential_gain=face.gain / neck.gain,
        lag_s=a.lag_s,
        band_hz=(float(band_hz[0]), float(band_hz[1])),
        fs=fs,
        duration_s=len(a.t) / fs,
        n_segments=specs["face"].n_segments,
        noise_floor=specs["face"].noise_floor,
        max_gap_s=gap,
        capture_fps=float(1.0 / np.median(np.diff(np.asarray(regions["face"][0], dtype=float)))),
    )

    face_neck = region_response(spectrum(a.regions["neck"], a.regions["face"], fs, nperseg), band_hz)
    background = resp["background"].gain / neck.gain if "background" in resp else None

    sub = {name[len(SUBREGION_PREFIX):]: r.gain for name, r in resp.items()
           if name.startswith(SUBREGION_PREFIX)}
    spatial, cv = {}, None
    if sub:
        mean = float(np.mean(list(sub.values())))
        spatial = {k: v / mean for k, v in sub.items()}
        cv = float(np.std(list(spatial.values())))

    lateral = {name: li for name, s in specs.items() if (li := lateral_index(s, band_hz)) is not None}
    contrast = cheek_contrast(lateral)
    contrast_se = None
    if contrast is not None:
        contrast_se = cheek_contrast_se({c: segs[c] for c in CHEEKS}, band_hz, n_blocks=jackknife_n_blocks)

    return Features(
        measurement=m,
        face_neck_coherence=face_neck.coherence,
        background_gain_ratio=background,
        spatial_field=spatial,
        spatial_cv=cv,
        lateral=lateral,
        cheek_contrast=contrast,
        cheek_contrast_se=contrast_se,
        face_neck_lightness=face_neck_lightness,
    )


def cheek_contrast_se(cheeks: Mapping[str, SegmentSpectra], band_hz: tuple[float, float] = PROBE_BAND_HZ,
                      *, n_blocks: int = 6) -> float | None:
    """Jackknife standard error of the cheek contrast, leaving out one block of segments at a time.

    It measures within-session noise only: drift between sessions (pose, light) is not in it, which
    is why M1 records genuine sessions on both sides of every attack session.
    """
    k = next(iter(cheeks.values())).n_segments
    if k < 2 * n_blocks:
        return None
    values = []
    for keep in jackknife_blocks(k, n_blocks):
        lat = {name: lateral_index(s.spectrum(keep), band_hz) for name, s in cheeks.items()}
        values.append(cheek_contrast(lat))
    return jackknife_se(values)


def measure(probe_t, probe_chroma, face_t, face_chroma, neck_t, neck_chroma, **kwargs) -> Measurement:
    """L1 and L2 (face vs neck only) from timestamped series on a shared clock."""
    regions = {"face": (face_t, face_chroma), "neck": (neck_t, neck_chroma)}
    return analyse(probe_t, probe_chroma, regions, **kwargs).measurement
