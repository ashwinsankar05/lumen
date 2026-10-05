"""Probe generator: one-time code -> deterministic, band-limited, isoluminant colour sequences.

The probe is a continuous function of time, not a list of frames. The emitter asks for the
colour at the moment each frame is actually presented and logs that (timestamp, colour) pair,
so dropped or late display frames are recorded as they really happened (PROJECT.md §8).

Layouts.
  split  (default) independent sequences on the left and right halves of the screen. A real face
         is a 3D object in front of the screen: a cheek turned towards the left half receives
         more of the left half's light, so each skin patch responds to the two sequences in
         proportions set by its geometry (features.lateral_index). The halves are seeded from
         nonce + "|left" and nonce + "|right", so they are statistically independent.
  single one sequence for the whole screen (kept for comparison runs; no L3).

Construction of one sequence. Each chroma axis (CIELAB a*, b*) is a sum of equal-amplitude
cosines on the frequency grid k / duration that falls inside the band (2-8 Hz by default), with
phases drawn from SHA-256 of the seed. Equal amplitudes give a flat in-band spectrum; random
phases make the sequence unpredictable without the nonce. L* is held constant, so CIE luminance
Y is constant: the probe is isoluminant by construction, as far as the display renders sRGB
faithfully.

Phases come from SHA-256 rather than NumPy's RNG so a sequence is reproducible from the nonce
alone, in any language and any NumPy version, and a session record can be checked
independently. The algorithm is identified as "lumen/probe/v1" in records; changing anything
here that alters the output requires a new identifier.
"""

from __future__ import annotations

import csv
import hashlib
import math
import secrets
from pathlib import Path
from typing import Literal

import numpy as np

from lumen.colour import lab_to_linear_srgb, linear_to_srgb

ALGORITHM = "lumen/probe/v1"
PROBE_BAND_HZ = (2.0, 8.0)
Layout = Literal["split", "single"]
LAYOUTS: tuple[Layout, ...] = ("split", "single")

_DOMAIN = b"lumen/probe/v1"
_DENSE_FS = 256.0  # grid used to normalise the peak and check the gamut
_CHUNK = 4096


def new_nonce() -> str:
    """A fresh one-time code (128 bits, hex)."""
    return secrets.token_hex(16)


def _as_bytes(nonce: str | bytes) -> bytes:
    b = nonce.encode("utf-8") if isinstance(nonce, str) else bytes(nonce)
    if not b:
        raise ValueError("nonce must not be empty")
    return b


def _uniforms(seed: bytes, n: int) -> np.ndarray:
    """n uniforms in [0, 1) from SHA-256(domain | len(seed) | seed | counter), 4 per digest."""
    prefix = _DOMAIN + len(seed).to_bytes(4, "big") + seed
    out: list[float] = []
    counter = 0
    while len(out) < n:
        digest = hashlib.sha256(prefix + counter.to_bytes(8, "big")).digest()
        out.extend(int.from_bytes(digest[i:i + 8], "big") / 2**64 for i in range(0, 32, 8))
        counter += 1
    return np.array(out[:n])


class ChromaSequence:
    """One isoluminant (a*, b*) sequence from one seed. See the module docstring."""

    def __init__(self, seed: bytes, *, duration_s: float, band_hz: tuple[float, float],
                 amplitude: float, lightness: float):
        lo, hi = band_hz
        if not 0 < lo < hi < _DENSE_FS / 2:
            raise ValueError(f"invalid band {band_hz}")
        if duration_s <= 0 or amplitude <= 0:
            raise ValueError("duration_s and amplitude must be positive")
        k = np.arange(math.ceil(lo * duration_s), math.floor(hi * duration_s) + 1)
        if k.size == 0:
            raise ValueError("duration too short for any component in the band")

        self.duration_s = float(duration_s)
        self.freqs_hz = k / self.duration_s
        self.phases = 2 * np.pi * _uniforms(seed, 2 * k.size).reshape(2, k.size)

        # Every component completes whole cycles over the duration, so a dense uniform grid over
        # [0, duration) is exactly one period and an inverse FFT evaluates it exactly.
        m = math.ceil(self.duration_s * _DENSE_FS)
        spectrum = np.zeros((2, m // 2 + 1), dtype=complex)
        spectrum[:, k] = (m / 2) * np.exp(1j * self.phases)
        dense = np.fft.irfft(spectrum, n=m, axis=-1).T  # (m, 2), unit-amplitude cosines summed
        self.scale = amplitude / np.abs(dense).max(axis=0)

        lab = np.concatenate([np.full((m, 1), float(lightness)), dense * self.scale], axis=-1)
        rgb = lab_to_linear_srgb(lab)
        if rgb.min() < 0 or rgb.max() > 1:
            raise ValueError(
                f"L*={lightness}, amplitude={amplitude} leaves the sRGB gamut; clipping would break "
                "isoluminance. Lower the amplitude or move L* towards mid-grey."
            )

    def chroma_at(self, t: np.ndarray) -> np.ndarray:
        """(a*, b*) at probe times t (already range-checked). Shape (N, 2) for flat t."""
        out = np.empty((t.size, 2))
        for i in range(0, t.size, _CHUNK):
            arg = 2 * np.pi * t[i:i + _CHUNK, None] * self.freqs_hz
            out[i:i + _CHUNK, 0] = np.cos(arg + self.phases[0]).sum(axis=1)
            out[i:i + _CHUNK, 1] = np.cos(arg + self.phases[1]).sum(axis=1)
        return out * self.scale


class Probe:
    """The colour field for one session, seeded by a one-time code.

    `chroma_at(t)` gives the probe channels at probe time t in [0, duration_s]: (a*, b*) for the
    single layout, (a*_left, b*_left, a*_right, b*_right) for split. The sequence never wraps,
    because a repeating probe would weaken the replay defence.
    """

    def __init__(
        self,
        nonce: str | bytes,
        *,
        layout: Layout = "split",
        duration_s: float = 60.0,
        band_hz: tuple[float, float] = PROBE_BAND_HZ,
        amplitude: float = 24.0,
        lightness: float = 70.0,
    ):
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}, not {layout!r}")
        seed = _as_bytes(nonce)
        self.layout: Layout = layout
        self.duration_s = float(duration_s)
        self.band_hz = (float(band_hz[0]), float(band_hz[1]))
        self.amplitude = float(amplitude)
        self.lightness = float(lightness)
        kw = dict(duration_s=duration_s, band_hz=self.band_hz, amplitude=amplitude, lightness=lightness)
        seeds = [seed + b"|left", seed + b"|right"] if layout == "split" else [seed]
        self.sequences = [ChromaSequence(s, **kw) for s in seeds]

    @property
    def halves(self) -> int:
        """Number of independently driven screen areas: 2 for split, 1 for single."""
        return len(self.sequences)

    @property
    def n_channels(self) -> int:
        """Probe chroma channels p, the inputs to the coherence analysis."""
        return 2 * self.halves

    def chroma_at(self, t) -> np.ndarray:
        """Probe chroma at probe time t (seconds). Shape (..., 2 * halves)."""
        t = np.asarray(t, dtype=float)
        if np.any(t < 0) or np.any(t > self.duration_s):
            raise ValueError(f"probe time outside [0, {self.duration_s}] s; the probe does not wrap")
        flat = t.reshape(-1)
        out = np.concatenate([s.chroma_at(flat) for s in self.sequences], axis=-1)
        return out.reshape(t.shape + (self.n_channels,))

    def lab_at(self, t) -> np.ndarray:
        """CIELAB at probe time t: shape (..., 3) for single, (..., 2, 3) (left, right) for split."""
        ab = self.chroma_at(t)
        ab = ab.reshape(ab.shape[:-1] + (self.halves, 2))
        lab = np.concatenate([np.full(ab.shape[:-1] + (1,), self.lightness), ab], axis=-1)
        return lab[..., 0, :] if self.layout == "single" else lab

    def srgb_at(self, t) -> np.ndarray:
        """Gamma-encoded sRGB in [0, 1] at probe time t, same leading shape as lab_at."""
        return linear_to_srgb(lab_to_linear_srgb(self.lab_at(t)))

    def base_lab(self) -> np.ndarray:
        """The colour shown before probe time zero (no chroma offset), shaped like lab_at."""
        base = np.array([self.lightness, 0.0, 0.0])
        return base if self.layout == "single" else np.stack([base, base])

    def params(self) -> dict:
        """Everything besides the nonce that regenerates this probe (stored in records)."""
        return {"algorithm": ALGORITHM, "layout": self.layout, "duration_s": self.duration_s,
                "band_hz": list(self.band_hz), "amplitude": self.amplitude, "lightness": self.lightness}

    @classmethod
    def from_params(cls, nonce: str | bytes, params: dict) -> "Probe":
        algorithm = params.get("algorithm", ALGORITHM)
        if algorithm != ALGORITHM:
            raise ValueError(f"unknown probe algorithm {algorithm!r}")
        return cls(nonce, layout=params.get("layout", "single"), duration_s=params["duration_s"],
                   band_hz=tuple(params["band_hz"]), amplitude=params["amplitude"],
                   lightness=params["lightness"])


def lab_to_chroma(lab) -> np.ndarray:
    """Emitted Lab per frame -> probe chroma channels: (N, 3) -> (N, 2); (N, 2, 3) -> (N, 4)."""
    lab = np.asarray(lab, dtype=float)
    return lab[..., 1:].reshape(len(lab), -1)


class EmissionLog:
    """What was actually emitted, one row per presented frame.

    Timestamps are on the caller's clock, the same monotonic clock the capture uses, so the two
    can be aligned by time. `t0` is the clock time of probe time zero.
    """

    COLUMNS = ("frame", "t", "probe_t", "L", "a", "b")
    SPLIT_COLUMNS = COLUMNS + ("L_r", "a_r", "b_r")

    def __init__(self, probe: Probe, t0: float):
        self.probe = probe
        self.t0 = float(t0)
        self.columns = self.SPLIT_COLUMNS if probe.halves == 2 else self.COLUMNS
        self._rows: list[tuple] = []

    def emit(self, t: float) -> np.ndarray:
        """Record the frame presented at clock time t; return its sRGB colour(s) in [0, 1]."""
        t = float(t)
        if self._rows and t <= self._rows[-1][1]:
            raise ValueError("emission timestamps must be strictly increasing")
        lab = self.probe.lab_at(t - self.t0)
        self._rows.append((len(self._rows), t, t - self.t0, *map(float, lab.ravel())))
        return linear_to_srgb(lab_to_linear_srgb(lab))

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def t(self) -> np.ndarray:
        return np.array([r[1] for r in self._rows])

    @property
    def lab(self) -> np.ndarray:
        """Emitted Lab per frame: (N, 3) single, (N, 2, 3) split."""
        shape = (-1, 2, 3) if self.probe.halves == 2 else (-1, 3)
        return np.array([r[3:] for r in self._rows]).reshape(shape)

    @property
    def chroma(self) -> np.ndarray:
        """Probe chroma per frame, the probe input to features: (N, 2) single, (N, 4) split."""
        return lab_to_chroma(self.lab)

    def save_csv(self, path: str | Path) -> None:
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.columns)
            writer.writerows([r[0], *(repr(v) for v in r[1:])] for r in self._rows)

    @staticmethod
    def load_csv(path: str | Path) -> dict[str, np.ndarray]:
        """Column name -> array, as written by save_csv."""
        with open(path, newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            rows = [[float(v) for v in row] for row in reader]
        data = np.array(rows).reshape(-1, len(header))
        cols = {name: data[:, i] for i, name in enumerate(header)}
        cols["frame"] = cols["frame"].astype(int)
        return cols
