"""Deep check: physical measurements beyond L1-L3, aimed at live face swaps.

Separate from the verdict path (decide.py, the demo and the call launcher do not use it). It
re-measures a session from its saved video or record and reports physical quantities that a
face swap would have to reproduce, each against the range seen in genuine sessions.

Neck-referenced lean. L3 compares the cheek contrast with a fixed threshold, and the genuine
contrast moves between sessions with distance, head position and call compression, so the
threshold has to sit far below the genuine value. The neck is real skin that a face swap does
not touch, and it is a 3D surface too: its image-left and image-right halves lean towards the
screen halves they face, under the same distance, pose and codec as the cheeks. The ratio

    lean ratio = cheek contrast / neck contrast

cancels what the two share, and a swap that weakens only the face's lean lowers it.
Measured on 2026-10-06: FaceFusion kept about 70 % of the cheek contrast (docs/RESULTS.md).
Whether the ratio is steadier across genuine sessions than the raw contrast is an empirical
question; experiments/deepcheck/neck_reference.py measures it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from lumen.features import CHEEKS, align, lateral_index, segment_spectra
from lumen.probe import lab_to_chroma
from lumen.regions import Landmarks, RegionExtractor, face_frame, head_pose, region_masks, region_means

NECK_HALVES = ("neck.l", "neck.r")  # image-left, image-right
# Face-frame extent of each half (u in eye distances, v in eye-to-mouth distances): the neck band
# of regions.py, widened a little because the lean grows towards the sides, with a central gap so
# the halves do not share the throat's front.
_NECK_U = {"neck.l": (-0.75, -0.1), "neck.r": (0.1, 0.75)}
_NECK_V = (1.95, 2.7)


def neck_half_masks(lm: Landmarks, shape: tuple[int, ...], min_pixels: int = 40) -> dict[str, np.ndarray]:
    """uint8 masks for the image-left and image-right halves of the neck ({} if too small)."""
    h, w = shape[:2]
    ff = face_frame(lm)
    out = {}
    for name, (u0, u1) in _NECK_U.items():
        m = np.zeros((h, w), np.uint8)
        corners = [ff.at(u0, _NECK_V[0]), ff.at(u1, _NECK_V[0]), ff.at(u1, _NECK_V[1]), ff.at(u0, _NECK_V[1])]
        cv2.fillPoly(m, [np.round(np.array(corners)).astype(np.int32)], 255)
        expected = (u1 - u0) * ff.ipd * (_NECK_V[1] - _NECK_V[0]) * ff.em
        n = cv2.countNonZero(m)
        if n >= min_pixels and n >= 0.5 * expected:
            out[name] = m
    return out if len(out) == 2 else {}


class DeepExtractor(RegionExtractor):
    """RegionExtractor plus the two neck halves."""

    def process(self, bgr: np.ndarray) -> dict[str, np.ndarray] | None:
        s = min(1.0, self.work_width / bgr.shape[1])
        small = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else bgr
        self._scale = s
        self.last_pose = None
        lm = self.detector.detect(small)
        if lm is None:
            self._smoothed = None
            return None
        if self._smoothed is not None:
            a = self.alpha
            lm = Landmarks(a * lm.box + (1 - a) * self._smoothed.box,
                           a * lm.points + (1 - a) * self._smoothed.points, lm.score)
        self._smoothed = lm
        try:
            masks = region_masks(lm, small.shape)
            if masks:
                masks.update(neck_half_masks(lm, small.shape))
        except ValueError:
            return None
        if not masks:
            return None
        self.last_pose = head_pose(lm)
        return region_means(small, masks)


@dataclass(frozen=True)
class LeanMeasures:
    cheek_contrast: float | None   # image-right cheek minus image-left cheek lateral index (L3)
    neck_contrast: float | None    # the same for the neck halves
    lean_ratio: float | None       # cheek contrast / neck contrast
    lean_ratio_se: float | None    # jackknife over blocks of segments (within-session noise)
    lateral: dict[str, float]      # lateral index of every region
    n_segments: int


def _lean(segs: dict, keep=None) -> tuple[float | None, float | None, dict]:
    lat = {}
    for name, s in segs.items():
        li = lateral_index(s.spectrum(keep))
        if li is not None:
            lat[name] = li
    cheek = lat[CHEEKS[0]] - lat[CHEEKS[1]] if all(c in lat for c in CHEEKS) else None
    neck = lat[NECK_HALVES[1]] - lat[NECK_HALVES[0]] if all(n in lat for n in NECK_HALVES) else None
    return cheek, neck, lat


def lean_measures(probe_t, probe_chroma, regions: dict, *, fs: float = 30.0, segment_s: float = 2.0,
                  max_lag_s: float = 1.0, n_blocks: int = 6) -> LeanMeasures:
    """Cheek and neck lateral contrast, and their ratio, from region (timestamps, chroma) series."""
    a = align(probe_t, probe_chroma, regions, fs=fs, max_lag_s=max_lag_s)
    nper = int(round(segment_s * fs))
    names = [n for n in (*CHEEKS, *NECK_HALVES, "face", "neck") if n in a.regions]
    segs = {n: segment_spectra(a.probe, a.regions[n], fs, nper) for n in names}
    cheek, neck, lat = _lean(segs)
    ratio = cheek / neck if cheek is not None and neck else None
    se = None
    k = next(iter(segs.values())).n_segments
    if ratio is not None and k >= 2 * n_blocks:
        from lumen.features import jackknife_blocks, jackknife_se

        loo = []
        for keep in jackknife_blocks(k, n_blocks):
            c, n, _ = _lean(segs, keep)
            if c is not None and n:
                loo.append(c / n)
        se = jackknife_se(loo) if len(loo) == n_blocks else None
    return LeanMeasures(cheek, neck, ratio, se, lat, k)


def regions_from_video(video: str | Path, timestamps: np.ndarray, extractor: RegionExtractor | None = None,
                       ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Re-measure a saved session video: region name -> (timestamps, Lab (N, 3)).

    A region is kept only where it was found; frames without a face are skipped.
    """
    ex = extractor or DeepExtractor()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise OSError(f"cannot open {video}")
    series: dict[str, tuple[list, list]] = {}
    i = 0
    try:
        while i < len(timestamps):
            ok, frame = cap.read()
            if not ok:
                break
            out = ex.process(frame)
            if out:
                for name, lab in out.items():
                    ts, xs = series.setdefault(name, ([], []))
                    ts.append(timestamps[i])
                    xs.append(lab)
            i += 1
    finally:
        cap.release()
    return {k: (np.asarray(ts, float), np.asarray(xs, float)) for k, (ts, xs) in series.items()}


def lean_from_session(session_dir: str | Path, record: dict, *, max_gap_s: float = 0.5) -> LeanMeasures | None:
    """Lean measures of a saved session, re-measured from its video (needs --save-video)."""
    d = Path(session_dir)
    video, stamps = d / "video.mkv", d / "video_timestamps.csv"
    if not video.exists() or not stamps.exists():
        return None
    t = np.loadtxt(stamps, delimiter=",", ndmin=1)
    regions = regions_from_video(video, t)
    if "face" not in regions or "neck" not in regions or any(n not in regions for n in NECK_HALVES):
        return None
    # the longest stretch where the face was found without a long gap, as in session.reanalyse
    ft = regions["neck"][0]
    breaks = np.flatnonzero(np.diff(ft) > max_gap_s)
    starts, ends = np.r_[0, breaks + 1], np.r_[breaks, len(ft) - 1]
    best = int(np.argmax(ft[ends] - ft[starts]))
    lo, hi = ft[starts[best]], ft[ends[best]]
    clipped = {}
    for name, (ts, lab) in regions.items():
        sel = (ts >= lo) & (ts <= hi)
        if sel.sum() >= 0.8 * ((ft >= lo) & (ft <= hi)).sum():
            clipped[name] = (ts[sel], lab[sel, 1:])
    cfg = (record.get("extra") or {}).get("config") or {}
    return lean_measures(np.asarray(record["emitted"]["t"], float),
                         lab_to_chroma(np.asarray(record["emitted"]["lab"], float)), clipped,
                         fs=cfg.get("fs", 30.0), segment_s=cfg.get("segment_s", 2.0),
                         max_lag_s=cfg.get("max_lag_s", 1.0))
