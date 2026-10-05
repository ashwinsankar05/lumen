"""One verification session: probe -> emitter + capture -> regions -> features -> verdict -> record.

Threads: the grabber reads and timestamps frames; a processing thread turns frames into region
colours; the emitter runs on the thread that calls `run()` (it owns the OpenCV window). Every
timestamp comes from capture.clock.

Timeline:
  [0, warmup)            emitter shows the base colour; the camera's auto-exposure settles
  lock_at_s              exposure and white balance matched and locked (a few seconds of search)
  [warmup, +duration)    probe running; every presented frame logged
  then                   analysis, verdict, signed record saved under out_dir/<session id>/
"""

from __future__ import annotations

import csv
import queue
import secrets
import threading
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from lumen.capture import FrameGrabber, Source, clock, open_source
from lumen.decide import Thresholds, Verdict, decide, load_thresholds
from lumen.emitter import CvDisplay, Display, Emitter
from lumen.features import Features, analyse
from lumen.probe import EmissionLog, Probe, lab_to_chroma, new_nonce
from lumen.record import build_record, load_or_create_key, save_record, sign_record
from lumen.regions import REQUIRED, RegionExtractor

VIDEO_NAME = "video.mkv"         # lossless FFV1, frame times in video_timestamps.csv
EMIT_MODES = ("screen", "virtualcam")
OPTIONAL_MIN_FRACTION = 0.8      # optional regions are analysed only if present this often


@dataclass
class SessionConfig:
    source: str = "camera:0"
    layout: str = "split"
    duration_s: float = 45.0
    warmup_s: float = 6.0
    lock_at_s: float = 1.5         # into the warm-up
    amplitude: float = 24.0
    lightness: float = 70.0
    fullscreen: bool = True
    window_size: tuple[int, int] = (1280, 720)
    window_position: tuple[int, int] = (0, 0)
    emit: str = "screen"           # "screen": this display; "virtualcam": our outgoing call video
    emit_fps: float | None = None  # None: 60 on screen, 30 into a virtual camera
    fs: float = 30.0
    segment_s: float = 2.0
    max_lag_s: float = 1.0         # raised to 2 s in call mode: network and call buffering add delay
    lock_camera: bool = True
    save_video: bool = False
    label: str = ""
    out_dir: str = "data/sessions"
    thresholds_path: str = "data/thresholds.json"
    key_path: str = "data/keys/lumen_ed25519.pem"


@dataclass
class SessionResult:
    session_id: str
    label: str
    outcome: str  # done / stopped / escape / error
    verdict: Verdict
    features: Features | None
    capture: dict
    face_fraction: float
    pose: dict | None
    frames_captured: int
    frames_processed: int
    frames_dropped: int
    capture_fps: float
    processing_ms: float
    emitted_frames: int
    record_path: str | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["features"] = self.features.to_dict() if self.features else None
        return d


# --- pure helpers (also used by reanalysis) ----------------------------------------------------

def split_regions(t: np.ndarray, samples: list[dict | None]) -> tuple[dict, float]:
    """Per-frame region dicts -> (name -> (timestamps, Lab (N, 3)), usable-frame fraction).

    A frame is usable when face and neck are both present. Optional regions are kept only if
    present in most usable frames, each with its own timestamps.
    """
    usable = [i for i, s in enumerate(samples) if s and all(r in s for r in REQUIRED)]
    fraction = len(usable) / len(samples) if samples else 0.0
    present: dict[str, list[int]] = {}
    for i in usable:
        for name in samples[i]:
            present.setdefault(name, []).append(i)
    out = {}
    for name, idx in present.items():
        if name in REQUIRED or len(idx) >= OPTIONAL_MIN_FRACTION * len(usable):
            out[name] = (t[idx], np.array([samples[i][name] for i in idx], dtype=float).reshape(-1, 3))
    return out, fraction


def analyse_capture(log_t, log_lab, regions_lab: dict, cfg: SessionConfig) -> Features | None:
    """Features from Lab series, or None when there is too little data to measure at all."""
    min_samples = 2 * cfg.segment_s * cfg.fs
    if any(r not in regions_lab or len(regions_lab[r][0]) < min_samples for r in REQUIRED):
        return None
    lightness = float(np.mean(regions_lab["face"][1][:, 0]) / np.mean(regions_lab["neck"][1][:, 0]))
    try:
        return analyse(log_t, lab_to_chroma(log_lab),
                       {name: (ts, lab[:, 1:]) for name, (ts, lab) in regions_lab.items()},
                       fs=cfg.fs, segment_s=cfg.segment_s, max_lag_s=cfg.max_lag_s,
                       face_neck_lightness=round(lightness, 4))
    except ValueError:
        return None


def pose_summary(poses: list) -> dict | None:
    """Median head pose over the frames where a face was found (regions.Pose as tuples)."""
    found = np.array([p for p in poses if p is not None], dtype=float).reshape(-1, 3)
    if len(found) == 0:
        return None
    yaw = found[:, 0]
    return {
        "yaw": round(float(np.median(yaw)), 4),
        "yaw_iqr": round(float(np.subtract(*np.percentile(yaw, [75, 25]))), 4),
        "roll_deg": round(float(np.median(found[:, 1])), 2),
        "ipd_px": round(float(np.median(found[:, 2])), 2),
        "n": int(len(found)),
    }


def default_display(cfg: SessionConfig) -> Display:
    """The probe output for a session: a full-screen window, or a virtual camera for a call."""
    if cfg.emit == "virtualcam":
        from lumen.emitter import VirtualCamDisplay

        return VirtualCamDisplay(size=cfg.window_size, fps=cfg.emit_fps or 30.0)
    return CvDisplay(fullscreen=cfg.fullscreen, size=cfg.window_size, position=cfg.window_position)


def config_from_record(record: dict) -> SessionConfig:
    saved = (record.get("extra") or {}).get("config") or {}
    known = {f.name for f in fields(SessionConfig)}
    return SessionConfig(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in saved.items() if k in known})


def samples_from_record(record: dict) -> tuple[np.ndarray, list[dict | None]]:
    t = np.asarray(record["returned"]["t"], dtype=float)
    rows = record["returned"]["regions"]
    samples = [{k: np.asarray(r[i], dtype=float) for k, r in rows.items() if r[i] is not None} or None
               for i in range(len(t))]
    return t, samples


def reanalyse(record: dict) -> Features | None:
    """Recompute features from a saved record's series, exactly as the live session did."""
    t, samples = samples_from_record(record)
    regions_lab, _ = split_regions(t, samples)
    return analyse_capture(np.asarray(record["emitted"]["t"], dtype=float),
                           np.asarray(record["emitted"]["lab"], dtype=float), regions_lab,
                           config_from_record(record))


# --- the session -----------------------------------------------------------------------------

class Session:
    def __init__(
        self,
        config: SessionConfig | None = None,
        *,
        source_factory: Callable[[str], Source] = open_source,
        display_factory: Callable[[SessionConfig], Display] | None = None,
        extractor_factory: Callable[[], RegionExtractor] = RegionExtractor,
        thresholds: Thresholds | None = None,
        now: Callable[[], float] = clock,
    ):
        self.cfg = cfg = config or SessionConfig()
        self.source_factory = source_factory
        if cfg.emit not in EMIT_MODES:
            raise ValueError(f"emit must be one of {EMIT_MODES}, not {cfg.emit!r}")
        self.display_factory = display_factory or default_display
        self.extractor_factory = extractor_factory
        self.thresholds = thresholds if thresholds is not None else load_thresholds(cfg.thresholds_path)
        self.now = now

        stamp = datetime.now(timezone.utc)
        self.started_at = stamp.isoformat(timespec="seconds")
        self.id = f"{stamp:%Y%m%d-%H%M%S}-{cfg.label or 'session'}-{secrets.token_hex(2)}"
        self.nonce = new_nonce()
        self.probe = Probe(self.nonce, layout=cfg.layout, duration_s=cfg.duration_s,
                           amplitude=cfg.amplitude, lightness=cfg.lightness)
        self.log: EmissionLog | None = None
        self.state = "pending"
        self.result: SessionResult | None = None

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._t: list[float] = []
        self._samples: list[dict | None] = []
        self._pose: list[tuple | None] = []
        self._proc_ms: list[float] = []
        self._grabber: FrameGrabber | None = None
        self._metrics: tuple[float, dict | None] = (-1e9, None)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> SessionResult:
        cfg, notes = self.cfg, []
        self.state = "starting"
        self.log = EmissionLog(self.probe, t0=self.now() + cfg.warmup_s)

        try:
            if cfg.source.startswith("synthetic:"):
                source, extractor = self._synthetic(cfg.source.partition(":")[2])
            else:
                source, extractor = self.source_factory(cfg.source), self.extractor_factory()
        except Exception as exc:
            return self._finish("error", capture={"source": cfg.source, "notes": [str(exc)]},
                                capture_error=f"cannot open source: {exc}", notes=notes)

        grabber = self._grabber = FrameGrabber(source, now=self.now).start()
        lock_timer = None
        if cfg.lock_camera:
            lock_timer = threading.Timer(min(cfg.lock_at_s, cfg.warmup_s / 2), grabber.request_lock)
            lock_timer.start()
        processing = threading.Thread(target=self._process, args=(grabber, extractor),
                                      name="lumen-process", daemon=True)
        processing.start()

        self.state = "running"
        try:
            display = self.display_factory(cfg)
        except Exception as exc:  # e.g. no virtual camera driver
            grabber.stop()
            processing.join(timeout=2.0)
            source.close()
            if lock_timer:
                lock_timer.cancel()
            return self._finish("error", capture=source.report.to_dict(),
                                capture_error=f"cannot open the probe output: {exc}", notes=notes)
        fps = cfg.emit_fps or (30.0 if cfg.emit == "virtualcam" else 60.0)
        try:
            outcome = Emitter(self.log, display, fps=fps, now=self.now).run(self._watch(grabber))
        finally:
            notes.extend(getattr(display, "notes", []))
            display.close()
            if lock_timer:
                lock_timer.cancel()
        if grabber.error:
            outcome = "error"

        self.state = "analysing"
        grabber.stop()
        self._stop.set()
        processing.join(timeout=5.0)
        source.close()
        if grabber.locked_at is not None and grabber.locked_at > self.log.t0:
            notes.append(f"camera lock finished {grabber.locked_at - self.log.t0:.1f} s into the probe")
        if outcome in ("stopped", "escape"):
            notes.append(f"session ended early ({outcome})")
        return self._finish(outcome, capture=source.report.to_dict(), capture_error=grabber.error, notes=notes)

    def _synthetic(self, scenario: str):
        from lumen.synthetic import FixedDetector, SyntheticScene, SyntheticSource

        scene = SyntheticScene(scenario, lambda: (self.probe, self.log.t0), seed=int(self.nonce[:8], 16))
        return SyntheticSource(scene, fps=self.cfg.fs, now=self.now), RegionExtractor(FixedDetector())

    def _watch(self, grabber: FrameGrabber) -> threading.Event:
        """An event the emitter polls: set on operator stop, capture loss or end of a file."""
        ev = threading.Event()

        def watch():
            while not ev.is_set():
                if self._stop.is_set() or grabber.error or grabber.ended:
                    ev.set()
                    return
                self._stop.wait(0.05)

        threading.Thread(target=watch, name="lumen-watch", daemon=True).start()
        return ev

    def _process(self, grabber: FrameGrabber, extractor: RegionExtractor) -> None:
        writer, video_t = None, []
        out = Path(self.cfg.out_dir) / self.id
        while not (self._stop.is_set() and grabber.frames.empty()):
            try:
                frame = grabber.frames.get(timeout=0.1)
            except queue.Empty:
                if not grabber.running and grabber.frames.empty():
                    break
                continue
            started = clock()
            sample = extractor.process(frame.image)
            pose = extractor.last_pose
            with self._lock:
                self._t.append(frame.t)
                self._samples.append(sample)
                self._pose.append((pose.yaw, pose.roll_deg, pose.ipd_px) if sample and pose else None)
                self._proc_ms.append((clock() - started) * 1e3)
            if self.cfg.save_video:
                if writer is None:
                    out.mkdir(parents=True, exist_ok=True)
                    h, w = frame.image.shape[:2]
                    # Lossless, so replay drills and codec experiments add the only compression.
                    writer = cv2.VideoWriter(str(out / VIDEO_NAME), cv2.VideoWriter_fourcc(*"FFV1"),
                                             self.cfg.fs, (w, h))
                writer.write(frame.image)
                video_t.append(frame.t)
        if writer is not None:
            writer.release()
            with open(out / "video_timestamps.csv", "w", newline="") as fh:
                csv.writer(fh).writerows([[repr(t)] for t in video_t])

    def _probe_window(self) -> tuple[np.ndarray, list, list]:
        """Captured frames inside the probe period: timestamps, region samples, poses."""
        with self._lock:
            t = np.array(self._t)
            samples, poses = list(self._samples), list(self._pose)
        if self.log is None or not len(t):
            return np.zeros(0), [], []
        keep = (t >= self.log.t0) & (t <= self.log.t0 + self.probe.duration_s)
        return t[keep], [s for s, k in zip(samples, keep) if k], [p for p, k in zip(poses, keep) if k]

    def _finish(self, outcome: str, *, capture: dict, capture_error: str | None, notes: list) -> SessionResult:
        cfg = self.cfg
        t, samples, poses = self._probe_window()
        regions_lab, fraction = split_regions(t, samples)
        pose = pose_summary(poses)
        features = None
        if self.log is not None and len(self.log) > 1:
            features = analyse_capture(*self.log.arrays(), regions_lab, cfg)
        verdict = decide(features, self.thresholds, face_fraction=fraction, capture_error=capture_error, pose=pose)

        record_path = None
        if self.log is not None and len(self.log) > 0:
            out = Path(cfg.out_dir) / self.id
            out.mkdir(parents=True, exist_ok=True)
            self.log.save_csv(out / "emission.csv")
            names = sorted({k for s in samples if s for k in s})
            record = build_record(
                session_id=self.id, started_at=self.started_at, nonce=self.nonce, log=self.log,
                capture_t=t, regions={k: [s.get(k) if s else None for s in samples] for k in names},
                pose=poses, capture=capture, features=features.to_dict() if features else None,
                verdict=verdict.to_dict(), thresholds=self.thresholds.to_dict(),
                extra={"label": cfg.label, "outcome": outcome, "face_fraction": fraction,
                       "pose_summary": pose, "notes": notes, "config": asdict(cfg)},
            )
            signed = sign_record(record, load_or_create_key(cfg.key_path))
            record_path = str(save_record(signed, out / "record.json"))

        g = self._grabber
        with self._lock:
            proc = float(np.mean(self._proc_ms)) if self._proc_ms else 0.0
            processed = len(self._samples)
        self.result = SessionResult(
            session_id=self.id, label=cfg.label, outcome=outcome, verdict=verdict, features=features,
            capture=capture, face_fraction=fraction, pose=pose,
            frames_captured=g.count if g else 0, frames_processed=processed,
            frames_dropped=g.dropped if g else 0, capture_fps=g.fps() if g else 0.0,
            processing_ms=proc, emitted_frames=len(self.log) if self.log else 0,
            record_path=record_path, notes=notes,
        )
        self.state = "error" if outcome == "error" else "done"
        return self.result

    # --- live view for the console ---------------------------------------------------------

    def snapshot(self, since: int = 0, window_s: float = 20.0) -> dict:
        """Samples captured since index `since`, plus metrics over the last window_s seconds."""
        with self._lock:
            t = np.array(self._t[since:])
            samples = self._samples[since:]
            total = len(self._t)
        t0 = self.log.t0 if self.log else 0.0
        rows = []
        for ti, s in zip(t, samples):
            pt = ti - t0
            probe = None
            if self.log is not None and 0 <= pt <= self.probe.duration_s:
                probe = [round(float(v), 3) for v in self.probe.chroma_at(pt)]
            rows.append({
                "t": round(float(pt), 3),
                "probe": probe,
                "face": None if not s or "face" not in s else [round(float(v), 3) for v in s["face"][1:]],
                "neck": None if not s or "neck" not in s else [round(float(v), 3) for v in s["neck"][1:]],
            })
        g = self._grabber
        return {
            "session_id": self.id,
            "state": self.state,
            "layout": self.cfg.layout,
            "next": total,
            "elapsed_s": round(self.now() - t0, 2) if self.log else 0.0,
            "duration_s": self.cfg.duration_s,
            "capture_fps": round(g.fps(), 1) if g else 0.0,
            "dropped": g.dropped if g else 0,
            "samples": rows,
            "metrics": self._rolling_metrics(window_s),
        }

    def _rolling_metrics(self, window_s: float) -> dict | None:
        last, cached = self._metrics
        if self.now() - last < 1.0:
            return cached
        metrics = None
        if self.log is not None and len(self.log) > 2:
            t, samples, _ = self._probe_window()
            if len(t):
                keep = t >= t[-1] - window_s
                regions_lab, fraction = split_regions(t[keep], [s for s, k in zip(samples, keep) if k])
                log_t, log_lab = self.log.arrays()  # the emitter is still appending
                cut = log_t >= t[-1] - window_s - self.cfg.max_lag_s - 1
                f = analyse_capture(log_t[cut], log_lab[cut], regions_lab,
                                    replace(self.cfg, segment_s=min(self.cfg.segment_s, 2.0)))
                if f is not None:
                    m = f.measurement
                    metrics = {
                        "face_coherence": round(m.face.coherence, 3),
                        "neck_coherence": round(m.neck.coherence, 3),
                        "differential_gain": round(m.differential_gain, 3),
                        "cheek_contrast": None if f.cheek_contrast is None else round(f.cheek_contrast, 3),
                        "noise_floor": round(m.noise_floor, 3),
                        "lag_s": round(m.lag_s, 3),
                        "face_fraction": round(fraction, 3),
                        "window_s": round(m.duration_s, 1),
                    }
        self._metrics = (self.now(), metrics)
        return metrics
