"""Does the probe survive a video-call codec? Re-encode captures through H.264 / VP8 and re-measure.

A call compresses the subject's video before LUMEN sees it. The probe is a small chroma
modulation, exactly what a low-bitrate encoder is tempted to quantise away or skip as "no change"
(PROJECT.md §9). This re-encodes frames through the codecs calls use, decodes them, and runs the
normal analysis, so each condition can be compared with the uncompressed original.

    python experiments/codec_survival/run.py --synthetic
        SIMULATED scenes (genuine, swap_inherit). Plumbing and plausibility only: never quote
        these numbers as measurements.
    python experiments/codec_survival/run.py --session <id> [--session <id> ...]
        A real session recorded with --save-video (lossless video.mkv + video_timestamps.csv).
        This is a measurement of codec loss on real footage; record it in docs/RESULTS.md with the
        session's own conditions.

Output: a Markdown table on stdout and results/<timestamp>/{results.md, results.json}.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

import cv2
import numpy as np

from lumen.features import Features
from lumen.probe import EmissionLog, Probe
from lumen.record import load_record, verify_record
from lumen.regions import RegionExtractor
from lumen.session import SessionConfig, VIDEO_NAME, analyse_capture, config_from_record, split_regions
from lumen.synthetic import SIZE, FixedDetector, SyntheticScene

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Codec:
    name: str
    encoder: str | None  # None: no compression (the reference)
    container: str = "mp4"
    bitrate: int = 0     # bits per second
    options: tuple = ()


# Bitrates are for a 320x240 tile, roughly what a call sends for a small participant view.
CODECS = [
    Codec("none", None),
    Codec("h264-600k", "libx264", "mp4", 600_000, (("preset", "veryfast"), ("tune", "zerolatency"))),
    Codec("h264-300k", "libx264", "mp4", 300_000, (("preset", "veryfast"), ("tune", "zerolatency"))),
    Codec("h264-150k", "libx264", "mp4", 150_000, (("preset", "veryfast"), ("tune", "zerolatency"))),
    Codec("h264-80k", "libx264", "mp4", 80_000, (("preset", "veryfast"), ("tune", "zerolatency"))),
    Codec("vp8-300k", "libvpx", "webm", 300_000, (("deadline", "realtime"), ("cpu-used", "8"))),
    Codec("vp8-150k", "libvpx", "webm", 150_000, (("deadline", "realtime"), ("cpu-used", "8"))),
]


def roundtrip(frames: Iterable[np.ndarray], codec: Codec, fs: float, scale_bitrate: float = 1.0) -> Iterator[np.ndarray]:
    """Encode BGR frames with `codec` and yield the decoded BGR frames, in order.

    scale_bitrate rescales the nominal bitrate (set for 320x240) to the actual frame size.
    """
    if codec.encoder is None:
        yield from frames
        return
    import av  # experiments-only dependency

    buf = io.BytesIO()
    out = av.open(buf, mode="w", format=codec.container)
    stream = None
    for img in frames:
        if stream is None:
            stream = out.add_stream(codec.encoder, rate=int(round(fs)))
            stream.width, stream.height = img.shape[1], img.shape[0]
            stream.pix_fmt = "yuv420p"  # what calls send: chroma at half resolution
            stream.bit_rate = int(codec.bitrate * scale_bitrate)
            stream.options = dict(codec.options)
        for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
            out.mux(packet)
    if stream is None:
        out.close()
        return
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()
    buf.seek(0)
    with av.open(buf, mode="r") as inp:
        for frame in inp.decode(video=0):
            yield frame.to_ndarray(format="bgr24")


def encoded_size(frames: list[np.ndarray], codec: Codec, fs: float) -> int:
    """Bytes the encoder produced (to report the achieved bitrate)."""
    import av

    buf = io.BytesIO()
    out = av.open(buf, mode="w", format=codec.container)
    stream = out.add_stream(codec.encoder, rate=int(round(fs)))
    stream.width, stream.height, stream.pix_fmt = frames[0].shape[1], frames[0].shape[0], "yuv420p"
    stream.bit_rate, stream.options = codec.bitrate, dict(codec.options)
    for img in frames:
        for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()
    return len(buf.getvalue())


def _features(t: np.ndarray, decoded: Iterable[np.ndarray], extractor: RegionExtractor,
              log_t, log_lab, cfg: SessionConfig) -> tuple[Features | None, float]:
    samples = [extractor.process(img) for img in decoded]
    if len(samples) != len(t):
        raise RuntimeError(f"codec returned {len(samples)} frames for {len(t)} inputs")
    regions, fraction = split_regions(t, samples)
    return analyse_capture(log_t, log_lab, regions, cfg), fraction


def synthetic_case(scenario: str, codec: Codec, *, duration: float = 40.0, seed: int = 0) -> dict:
    """SIMULATED: render a scene, push it through the codec, measure."""
    fs = 30.0
    probe = Probe(f"codec-{scenario}-{seed}", duration_s=duration)
    t0 = 10.0
    log = EmissionLog(probe, t0)
    for t in t0 + np.arange(int(duration * 60)) / 60:
        log.emit(t)
    scene = SyntheticScene(scenario, lambda: (probe, t0), seed=seed)
    ts = t0 + 0.4 + np.arange(int((duration - 1) * fs)) / fs
    decoded = roundtrip((scene.render(t) for t in ts), codec, fs)
    f, fraction = _features(ts, decoded, RegionExtractor(FixedDetector()), log.t, log.lab, SessionConfig())
    return _row(f"SIMULATED {scenario}", codec, f, fraction)


def session_case(record_dir: Path, codec: Codec) -> dict:
    """Measured: a real lossless recording through the codec, analysed like the live session."""
    rec = load_record(record_dir / "record.json")
    if not verify_record(rec).ok:
        raise SystemExit(f"{record_dir.name}: record does not verify")
    cfg = config_from_record(rec)
    with open(record_dir / "video_timestamps.csv", newline="") as fh:
        t_all = np.array([float(r[0]) for r in csv.reader(fh)])
    log_t = np.asarray(rec["emitted"]["t"], dtype=float)
    log_lab = np.asarray(rec["emitted"]["lab"], dtype=float)
    p = rec["probe"]
    keep = (t_all >= log_t[0] - 0.5) & (t_all <= log_t[0] + p["duration_s"] + 1.0)

    def frames():
        cap = cv2.VideoCapture(str(record_dir / VIDEO_NAME))
        for i in range(len(t_all)):
            ok, img = cap.read()
            if not ok:
                break
            if keep[i]:
                yield img
        cap.release()

    w = rec["capture"].get("width") or SIZE[1]
    h = rec["capture"].get("height") or SIZE[0]
    scale = (w * h) / (SIZE[0] * SIZE[1])
    decoded = roundtrip(frames(), codec, cfg.fs, scale_bitrate=scale)
    f, fraction = _features(t_all[keep], decoded, RegionExtractor(), log_t, log_lab, cfg)
    label = (rec.get("extra") or {}).get("label") or record_dir.name
    if rec.get("capture", {}).get("simulated"):
        label = f"SIMULATED {label}"
    return _row(label, codec, f, fraction, scale)


def _row(label: str, codec: Codec, f: Features | None, fraction: float, scale: float = 1.0) -> dict:
    m = f.measurement if f else None
    return {
        "case": label,
        "codec": codec.name,
        "bitrate_kbps": round(codec.bitrate * scale / 1000) if codec.encoder else None,
        "face_fraction": round(fraction, 3),
        "face_coherence": m and round(m.face.coherence, 3),
        "neck_coherence": m and round(m.neck.coherence, 3),
        "noise_floor": m and round(m.noise_floor, 3),
        "gain": m and round(m.differential_gain, 3),
        "cheek_contrast": None if f is None or f.cheek_contrast is None else round(f.cheek_contrast, 3),
        "cheek_contrast_se": None if f is None or f.cheek_contrast_se is None else round(f.cheek_contrast_se, 3),
    }


def markdown(rows: list[dict]) -> str:
    def v(x):
        return "–" if x is None else x

    def contrast(r):
        if r["cheek_contrast"] is None:
            return "–"
        se = "" if r["cheek_contrast_se"] is None else f" ± {r['cheek_contrast_se']:.3f}"
        return f"{r['cheek_contrast']:+.3f}{se}"

    lines = ["| Case | Codec | kbps | Face coh. | Neck coh. | Floor | Gain | Cheek contrast ± SE |",
             "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['codec']} | {v(r['bitrate_kbps'])} | {v(r['face_coherence'])} | "
              f"{v(r['neck_coherence'])} | {v(r['noise_floor'])} | {v(r['gain'])} | {contrast(r)} |" for r in rows]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--synthetic", action="store_true", help="SIMULATED scenes (plumbing/plausibility)")
    p.add_argument("--session", action="append", default=[], help="session id recorded with --save-video")
    p.add_argument("--codec", action="append", help=f"subset of: {', '.join(c.name for c in CODECS)}")
    p.add_argument("--out-dir", default=SessionConfig().out_dir)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the table uses ± and –
    if not a.synthetic and not a.session:
        p.error("give --synthetic and/or --session <id>")
    codecs = [c for c in CODECS if not a.codec or c.name in a.codec]

    rows = []
    for codec in codecs:
        if a.synthetic:
            for scenario in ("genuine", "swap_inherit"):
                rows.append(synthetic_case(scenario, codec))
                print(f"  {rows[-1]['case']:28s} {codec.name:10s} cheek {rows[-1]['cheek_contrast']}", file=sys.stderr)
        for sid in a.session:
            rows.append(session_case(Path(a.out_dir) / sid, codec))
            print(f"  {rows[-1]['case']:28s} {codec.name:10s} cheek {rows[-1]['cheek_contrast']}", file=sys.stderr)

    table = markdown(rows)
    print(table)
    out = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.md").write_text(table, encoding="utf-8")
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
