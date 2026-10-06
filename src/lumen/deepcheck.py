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
question; experiments/deepcheck/neck_reference.py measures it (measured 2026-10-06: it is not).

3D relief. The nose and chin stand out towards the screen and the forehead and cheeks slope away,
so on a real face the face patches respond to the probe with clearly different strengths. A
generator that recolours its output face from the frame's overall colour spreads the probe evenly
over the face. Relief is the spread (coefficient of variation) of the patches' probe gains, with
each gain estimated noise-free from two independent halves of the recording.

Judgement (`judge`): a face swap is suspected only when BOTH 3D signs are low, the lateral lean
(L3's cheek contrast) and the relief, each against a bound from genuine sessions only
(mean - k sqrt(sd^2 + SE^2), k = 2). Two independent deficits are required, so each bound can sit
closer to genuine than L3's single 3-sigma bound without more false alarms.
Validated so far only with the probe on the subject's own screen; through a Google Meet call the
relief did not separate (compression), see docs/RESULTS.md.
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


# --- 3D relief and the two-sign judgement ------------------------------------------------------

SUBREGIONS = ("face.forehead", "face.nose", "face.cheek_l", "face.cheek_r", "face.chin")
BAND_HZ = (2.0, 8.0)
DEFAULT_K = 2.0


def _clean_gain2(seg, keep: np.ndarray | None = None, band_hz=BAND_HZ) -> float:
    """Squared probe gain of one region with the noise bias removed.

    The transfer is estimated separately on two interleaved halves of the (kept) segments, in
    blocks of two so that 50 %-overlapping neighbours fall in the same half; the noise in the two
    estimates is independent, so their cross-product keeps the response and averages the noise out.
    """
    k = seg.n_segments
    idx = np.arange(k) if keep is None else np.flatnonzero(keep)
    first = (np.arange(len(idx)) // 2) % 2 == 0
    a, b = np.zeros(k, bool), np.zeros(k, bool)
    a[idx[first]], b[idx[~first]] = True, True
    sa, sb = seg.spectrum(a), seg.spectrum(b)
    m = (sa.f >= band_hz[0]) & (sa.f <= band_hz[1])
    return float(np.einsum("fcp,fcp->f", sa.transfer[m], sb.transfer[m].conj()).real.mean())


def relief(segs: dict, keep: np.ndarray | None = None) -> float | None:
    """Coefficient of variation of the face patches' noise-corrected probe gains (None if < 4)."""
    g = np.array([np.sqrt(max(_clean_gain2(segs[n], keep), 0.0)) for n in SUBREGIONS if n in segs])
    if len(g) < 4 or g.mean() <= 0:
        return None
    return float(g.std() / g.mean())


@dataclass(frozen=True)
class DeepMeasures:
    session: str
    cheek_contrast: float | None
    cheek_contrast_se: float | None
    relief: float | None
    relief_se: float | None
    neck_coherence: float
    noise_floor: float
    simulated: bool = False
    fast_slow_ratio: float | None = None  # neck coherence 5-8 Hz / 2-4.5 Hz (temporal fidelity)
    lag_s: float | None = None            # light-to-video delay estimated from the neck
    emit: str = "screen"                  # "virtualcam": through a video call


# Temporal fidelity (EXPERIMENTAL, not part of any verdict). A real face reflects the probe at once,
# so the neck follows the fast half of the band nearly as well as the slow half, and the delay is the
# camera's; a face-swap pipeline renders every frame, adding delay and smearing fast changes.
# Found after the fact in the link swap test and confirmed on the earlier local swap records
# (docs/RESULTS.md, 2026-10-07): swap 0.29-0.57 and 0.30-0.33 s; swap off 0.61-0.80 and <= 0.17 s;
# direct webcam 0.83-0.97 and <= 0.13 s. The limits below were FIXED ON 2026-10-07, BEFORE any new
# session, so the next sessions test them rather than fit them. Do not move them after seeing data.
FAST_BAND_HZ, SLOW_BAND_HZ = (5.0, 8.0), (2.0, 4.5)
TEMPORAL_RATIO_MIN = 0.60
TEMPORAL_LAG_MAX_S = 0.25


@dataclass(frozen=True)
class TemporalVerdict:
    decision: str   # "pipeline" (suspected) | "direct" | "abstain"
    reason: str


def judge_temporal(m: DeepMeasures | None, neck_floor_multiple: float = 4.0) -> TemporalVerdict:
    """EXPERIMENTAL: does the video look like it went through a slow, smearing pipeline?"""
    if m is None or m.fast_slow_ratio is None or m.lag_s is None:
        return TemporalVerdict("abstain", "not measurable")
    if m.emit == "virtualcam":
        return TemporalVerdict("abstain", "not valid through a video call (the call smears every session)")
    if m.neck_coherence < max(0.15, neck_floor_multiple * m.noise_floor):
        return TemporalVerdict("abstain", "probe too weak at the neck")
    slow = m.fast_slow_ratio < TEMPORAL_RATIO_MIN
    late = m.lag_s > TEMPORAL_LAG_MAX_S
    if slow and late:
        return TemporalVerdict("pipeline", f"fast flicker lost (ratio {m.fast_slow_ratio:.2f} < "
                                           f"{TEMPORAL_RATIO_MIN}) and delayed ({m.lag_s:.2f} s > "
                                           f"{TEMPORAL_LAG_MAX_S} s): a processing pipeline is suspected")
    return TemporalVerdict("direct", f"ratio {m.fast_slow_ratio:.2f}, delay {m.lag_s:.2f} s: "
                                     + ("one of two signs only" if slow or late else "consistent with a direct camera"))


def deep_measures(record: dict, *, max_gap_s: float = 0.5, n_blocks: int = 6) -> DeepMeasures | None:
    """Lean and relief of a saved session, from its record (no video needed)."""
    from lumen.features import band_mean, cheek_contrast_se, jackknife_blocks, jackknife_se
    from lumen.session import config_from_record, longest_clean_stretch, samples_from_record, split_regions

    cfg = config_from_record(record)
    t, samples = samples_from_record(record)
    keep = longest_clean_stretch(t, samples, max_gap_s)
    regions_lab, _ = split_regions(t[keep], [s for s, k in zip(samples, keep) if k])
    min_n = 2 * cfg.segment_s * cfg.fs
    if any(r not in regions_lab or len(regions_lab[r][0]) < min_n for r in ("face", "neck")):
        return None
    probe = lab_to_chroma(np.asarray(record["emitted"]["lab"], float))
    a = align(np.asarray(record["emitted"]["t"], float), probe,
              {k: (ts, lab[:, 1:]) for k, (ts, lab) in regions_lab.items()}, fs=cfg.fs, max_lag_s=cfg.max_lag_s)
    nper = int(round(cfg.segment_s * cfg.fs))
    segs = {k: segment_spectra(a.probe, x, cfg.fs, nper) for k, x in a.regions.items()}
    neck = segs["neck"].spectrum()

    cheek, cheek_se = None, None
    if all(c in segs for c in CHEEKS):
        lat = {c: lateral_index(segs[c].spectrum()) for c in CHEEKS}
        if all(v is not None for v in lat.values()):
            cheek = lat[CHEEKS[0]] - lat[CHEEKS[1]]
            cheek_se = cheek_contrast_se({c: segs[c] for c in CHEEKS}, n_blocks=n_blocks)

    rel, rel_se = relief(segs), None
    k = neck.n_segments
    if rel is not None and k >= 2 * n_blocks:
        loo = [relief(segs, keep) for keep in jackknife_blocks(k, n_blocks)]
        rel_se = jackknife_se(loo) if all(v is not None for v in loo) else None

    slow_coh = float(band_mean(neck.f, neck.combined, SLOW_BAND_HZ))
    fast_coh = float(band_mean(neck.f, neck.combined, FAST_BAND_HZ))
    return DeepMeasures(
        session=record.get("session_id", "?"),
        cheek_contrast=cheek, cheek_contrast_se=cheek_se, relief=rel, relief_se=rel_se,
        neck_coherence=float(band_mean(neck.f, neck.combined)), noise_floor=neck.noise_floor,
        simulated=bool(record.get("capture", {}).get("simulated")),
        fast_slow_ratio=fast_coh / slow_coh if slow_coh > 0 else None, lag_s=float(a.lag_s),
        emit=str(cfg.emit),
    )


@dataclass(frozen=True)
class DeepThresholds:
    contrast_min: float      # oriented cheek contrast (sign applied)
    relief_min: float
    contrast_sign: int
    k: float
    n_genuine: int
    sessions: tuple[str, ...]

    def save(self, path) -> None:
        import json
        from dataclasses import asdict

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path) -> "DeepThresholds":
        import json

        d = json.loads(Path(path).read_text())
        d["sessions"] = tuple(d["sessions"])
        return cls(**d)


def _bound(values, ses, k: float) -> float:
    v = np.asarray(values, float)
    se = np.asarray([s or 0.0 for s in ses], float)
    return float(v.mean() - k * np.sqrt(v.std(ddof=1) ** 2 + np.mean(se ** 2)))


def calibrate_deep(genuine: list[DeepMeasures | None], k: float = DEFAULT_K) -> DeepThresholds:
    """Bounds from genuine sessions only (at least three, each with both measures)."""
    usable = [m for m in genuine if m and m.cheek_contrast is not None and m.relief is not None]
    if len(usable) < 3:
        raise ValueError(f"calibration needs at least 3 genuine sessions with both measures, got {len(usable)}")
    sign = 1 if np.mean([m.cheek_contrast for m in usable]) >= 0 else -1
    return DeepThresholds(
        contrast_min=round(_bound([sign * m.cheek_contrast for m in usable],
                                  [m.cheek_contrast_se for m in usable], k), 4),
        relief_min=round(_bound([m.relief for m in usable], [m.relief_se for m in usable], k), 4),
        contrast_sign=sign, k=k, n_genuine=len(usable), sessions=tuple(m.session for m in usable),
    )


@dataclass(frozen=True)
class DeepVerdict:
    decision: str   # "real" | "swap" | "abstain"
    reason: str
    lean_low: bool | None
    relief_low: bool | None


def judge(m: DeepMeasures | None, th: DeepThresholds, neck_floor_multiple: float = 4.0) -> DeepVerdict:
    if m is None:
        return DeepVerdict("abstain", "not enough data to measure", None, None)
    gate = max(0.15, neck_floor_multiple * m.noise_floor)
    if m.neck_coherence < gate:
        return DeepVerdict("abstain", f"probe too weak at the neck ({m.neck_coherence:.3f}, needs {gate:.3f})",
                           None, None)
    if m.cheek_contrast is None or m.relief is None:
        return DeepVerdict("abstain", "face patches not measurable (needs the two-sided probe and all patches)",
                           None, None)
    lean_low = th.contrast_sign * m.cheek_contrast < th.contrast_min
    relief_low = m.relief < th.relief_min
    if lean_low and relief_low:
        return DeepVerdict("swap", "the face lacks both 3D signs of a real face: the cheeks lean less towards "
                                   "their screen halves and the face patches respond too evenly", True, True)
    if lean_low or relief_low:
        which = "lean" if lean_low else "relief"
        return DeepVerdict("real", f"one 3D sign ({which}) is low, the other is normal: not enough to call a swap",
                           lean_low, relief_low)
    return DeepVerdict("real", "both 3D signs (lean and relief) are within the genuine range", False, False)


# --- command line: python -m lumen.deepcheck live|calibrate|check -----------------------------

SESSIONS_DIR = Path("data/sessions")


def _load(path: str | Path) -> dict:
    from lumen.record import load_record

    p = Path(path)
    return load_record(p / "record.json" if p.is_dir() else p)


def latest_sessions(n: int, label: str = "", sessions_dir: str | Path = SESSIONS_DIR) -> list[Path]:
    """The n newest real (not simulated) session folders whose name contains `label`, oldest first."""
    found = []
    for d in sorted(Path(sessions_dir).glob("*"), reverse=True):
        if not (d / "record.json").exists() or (label and label not in d.name):
            continue
        try:
            if _load(d).get("capture", {}).get("simulated"):
                continue
        except (OSError, ValueError, KeyError):
            continue
        found.append(d)
        if len(found) == n:
            break
    return found[::-1]


def describe(m: DeepMeasures | None, th: DeepThresholds | None) -> str:
    """One line: the two 3D signs, and the deep verdict when calibrated."""
    if m is None:
        return "not enough data to measure"
    sign = th.contrast_sign if th else 1
    lean = "-" if m.cheek_contrast is None else f"{sign * m.cheek_contrast:+.3f}"
    rel = "-" if m.relief is None else f"{m.relief:.3f}"
    sim = "  (SIMULATED)" if m.simulated else ""
    if th is None:
        return f"lean {lean}, relief {rel}: not calibrated yet (python -m lumen.deepcheck calibrate ...){sim}"
    v = judge(m, th)
    return (f"lean {lean} (needs >= {th.contrast_min}), relief {rel} (needs >= {th.relief_min})"
            f"  -> {v.decision.upper()}: {v.reason}{sim}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="python -m lumen.deepcheck",
                                description="3D lean + relief live-swap check (separate from the main verdict)")
    sub = p.add_subparsers(dest="cmd", required=True)

    lv = sub.add_parser("live", help="one session (probe full screen, L1-L3 verdict) plus the deep check; "
                                     "whoever runs it must have asked the subject first")
    lv.add_argument("--source", default="camera:0")
    lv.add_argument("--duration", type=float, default=90.0)
    lv.add_argument("--amplitude", type=float, default=34.0)
    lv.add_argument("--label", default="deep")
    lv.add_argument("--save-video", action="store_true", help="also save the captured video (lossless)")
    lv.add_argument("--no-lock", action="store_true", help="leave camera auto-exposure alone (virtual cameras)")
    lv.add_argument("--thresholds", default="data/thresholds.json", help="L1-L3 calibration")
    lv.add_argument("--deep-thresholds", default="data/deep_thresholds.json")
    lv.add_argument("--send-to", help="also send the record to a relay receiver, e.g. http://192.168.1.20:8765 "
                                      "(lab mode, see lumen/relay.py)")
    lv.add_argument("--token", help="the receiver's token (with --send-to)")

    c = sub.add_parser("calibrate", help="bounds from GENUINE sessions only")
    c.add_argument("records", nargs="*", help="session folders or record files")
    c.add_argument("--latest", type=int, help="use the N newest sessions instead (with --label)")
    c.add_argument("--label", default="", help="with --latest: only sessions whose name contains this")
    c.add_argument("--out", default="data/deep_thresholds.json")
    c.add_argument("--k", type=float, default=DEFAULT_K)

    j = sub.add_parser("check", help="judge saved sessions")
    j.add_argument("records", nargs="*")
    j.add_argument("--latest", type=int, help="judge the N newest sessions instead")
    j.add_argument("--label", default="")
    j.add_argument("--thresholds", default="data/deep_thresholds.json")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if a.cmd == "live":
        from lumen.__main__ import _print_result
        from lumen.session import Session, SessionConfig

        cfg = SessionConfig(source=a.source, duration_s=a.duration, amplitude=a.amplitude, label=a.label,
                            save_video=a.save_video, lock_camera=not a.no_lock, thresholds_path=a.thresholds)
        session = Session(cfg)
        print(f"session {session.id}: warm-up {cfg.warmup_s:g} s, then {cfg.duration_s:g} s of split probe. "
              "Esc aborts.")
        r = session.run()
        _print_result(r)
        th = DeepThresholds.load(a.deep_thresholds) if Path(a.deep_thresholds).exists() else None
        m = deep_measures(_load(r.record_path)) if r.record_path else None
        print(f"\n  DEEP CHECK (3D relief + lean): {describe(m, th)}")
        if a.send_to and r.record_path:
            from lumen.relay import send_record

            reply = send_record(a.send_to, a.token or "", r.record_path)
            if "error" in reply:
                print(f"\n  NOT SENT to {a.send_to}: {reply['error']} {reply.get('problems', '')}")
                print("  (the record is saved here; resend later with python -m lumen.relay send ...)")
            else:
                print(f"\n  SENT to {a.send_to}. Receiver's deep check: {reply['deep']}"
                      + (f" ({reply['note']})" if reply.get("note") else ""))
        return 0

    paths = [Path(x) for x in a.records]
    if a.latest:
        paths += latest_sessions(a.latest, a.label)
    if not paths:
        print("No sessions found" + (f" with '{a.label}' in the name" if a.label else "") +
              ": record some first (genuine calibration sessions for calibrate).")
        return 1

    if a.cmd == "calibrate":
        measures = [deep_measures(_load(x)) for x in paths]
        for x, m in zip(paths, measures):
            print(f"  {x.name:40s} {describe(m, None)}")
        try:
            th = calibrate_deep(measures, k=a.k)
        except ValueError as exc:
            print(f"Not calibrated: {exc}.")
            return 1
        th.save(a.out)
        print(f"calibrated from {th.n_genuine} genuine sessions: lean >= {th.contrast_min}, "
              f"relief >= {th.relief_min} (k = {th.k}) -> {a.out}")
        return 0

    th = DeepThresholds.load(a.thresholds) if Path(a.thresholds).exists() else None
    if th:
        print(f"bounds: lean >= {th.contrast_min}, relief >= {th.relief_min} (from {th.n_genuine} genuine sessions)")
    for x in paths:
        m = deep_measures(_load(x))
        print(f"{(m.session if m else x.name):36s} {describe(m, th)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
