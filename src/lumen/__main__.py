"""python -m lumen <command>

  serve                 operator console at http://127.0.0.1:8000
  run                   one session from the terminal; prints the verdict and saves a signed record
  smoke                 short capture check: real frame rate, exposure lock, face found
  cameras               list camera indices that open (real and virtual)
  calibrate ID [ID...]  set thresholds from genuine sessions recorded in today's conditions
  verify RECORD.json    check a record's signature and that its emission matches its nonce
  reanalyse RECORD.json recompute features and verdict from a saved record
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path


def _session_args(p: argparse.ArgumentParser, duration: float) -> None:
    p.add_argument("--source", default="camera:0",
                   help="camera:<n> | file:<video> | screen:<x>,<y>,<w>,<h> | mjpeg:<url> | synthetic:<scenario>")
    p.add_argument("--single", action="store_true", help="one probe sequence for the whole screen (no L3)")
    p.add_argument("--duration", type=float, default=duration, help="probe duration, seconds")
    p.add_argument("--amplitude", type=float, default=24.0, help="probe chroma amplitude (CIELAB units)")
    p.add_argument("--lightness", type=float, default=70.0, help="probe L* (constant)")
    p.add_argument("--label", default="", help="free text, e.g. genuine-1, swap-1")
    p.add_argument("--windowed", action="store_true", help="window instead of full-screen")
    p.add_argument("--monitor-x", type=int, default=0, help="x offset of the screen to show the probe on")
    p.add_argument("--save-video", action="store_true", help="also save the captured video (lossless)")
    p.add_argument("--max-lag", type=float, default=1.0, help="largest pipeline delay searched, seconds")
    p.add_argument("--no-lock", action="store_true", help="leave camera auto-exposure / auto-WB alone")


def _config(a):
    from lumen.session import SessionConfig

    return SessionConfig(source=a.source, layout="single" if a.single else "split", duration_s=a.duration,
                         amplitude=a.amplitude, lightness=a.lightness, label=a.label,
                         fullscreen=not a.windowed, window_position=(a.monitor_x, 0),
                         save_video=a.save_video, max_lag_s=a.max_lag, lock_camera=not a.no_lock)


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}"


def _print_result(r) -> None:
    v = r.verdict
    print(f"\n  VERDICT: {v.decision.upper()}{'  (provisional thresholds)' if v.provisional else ''}")
    print(f"  {v.reason}\n")
    for c in v.checks:
        mark = {True: "pass", False: "FAIL", None: " -- "}[c.passed]
        print(f"  [{mark}] {c.name:28s} {_fmt(c.value):>8s}   {c.threshold}")
    if r.features:
        m = r.features.measurement
        print(f"\n  band {m.band_hz[0]:g}-{m.band_hz[1]:g} Hz, K = {m.n_segments} Welch segments, "
              f"noise floor {m.noise_floor:.3f}, lag {m.lag_s:.3f} s, analysed {m.duration_s:.1f} s")
    print(f"  capture: {r.capture.get('source')}  {r.capture_fps:.1f} fps measured, {r.frames_dropped} dropped, "
          f"processing {r.processing_ms:.1f} ms/frame, face in {r.face_fraction:.0%} of frames")
    if r.capture.get("simulated"):
        print("  SIMULATED source: this is not a measurement")
    for note in r.capture.get("notes", []) + r.notes:
        print(f"    - {note}")
    print(f"  record: {r.record_path}")


def _calibration_inputs(session_ids):
    from lumen.record import load_record
    from lumen.session import SessionConfig, reanalyse

    out_dir = Path(SessionConfig().out_dir)
    feats, poses = [], []
    for sid in session_ids:
        rec = load_record(out_dir / sid / "record.json")
        if rec.get("capture", {}).get("simulated"):
            print(f"{sid}: simulated session; calibrate from real captures only", file=sys.stderr)
        f = reanalyse(rec)
        if f is None:
            raise SystemExit(f"{sid}: too little data to calibrate from")
        feats.append(f)
        poses.append((rec.get("extra") or {}).get("pose_summary"))
    return feats, poses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lumen", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    _session_args(sub.add_parser("run"), 45.0)
    _session_args(sub.add_parser("smoke"), 20.0)
    sub.add_parser("cameras").add_argument("--max-index", type=int, default=5)
    sub.add_parser("calibrate").add_argument("session_ids", nargs="+")
    sub.add_parser("verify").add_argument("record")
    sub.add_parser("reanalyse").add_argument("record")
    a = parser.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn

        from lumen.api import create_app

        print(f"LUMEN console: http://{a.host}:{a.port}")
        uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")
        return 0

    if a.cmd in ("run", "smoke"):
        from lumen.session import Session

        cfg = _config(a)
        if a.cmd == "smoke":
            cfg = replace(cfg, label=a.label or "smoke")
        session = Session(cfg)
        print(f"session {session.id}: warm-up {cfg.warmup_s:g} s, then {cfg.duration_s:g} s of "
              f"{cfg.layout} probe. Esc aborts.")
        r = session.run()
        if a.cmd == "smoke":
            print(f"\n  SMOKE TEST ({r.capture.get('source')})")
            print(f"  frame rate:        {r.capture_fps:.1f} fps measured (requested {r.capture.get('fps_requested')})")
            print(f"  exposure locked:   {r.capture.get('exposure_locked')}")
            print(f"  white bal. locked: {r.capture.get('white_balance_locked')}")
            print(f"  frames:            {r.frames_captured} captured, {r.frames_processed} processed, "
                  f"{r.frames_dropped} dropped; {r.emitted_frames} emitted")
            print(f"  face+neck found:   {r.face_fraction:.0%} of probe-period frames")
            print(f"  processing:        {r.processing_ms:.1f} ms/frame")
        _print_result(r)
        return 0

    if a.cmd == "cameras":
        import cv2

        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        for i in range(a.max_index + 1):
            cap = cv2.VideoCapture(i, backend)
            ok, img = cap.read() if cap.isOpened() else (False, None)
            if ok:
                print(f"camera:{i}  {img.shape[1]}x{img.shape[0]}  ({cap.getBackendName()})")
            cap.release()
        return 0

    if a.cmd == "calibrate":
        from lumen.decide import calibrate
        from lumen.session import SessionConfig

        feats, poses = _calibration_inputs(a.session_ids)
        th = calibrate(feats, poses=poses)
        th.save(SessionConfig().thresholds_path)
        print(json.dumps(th.to_dict(), indent=2))
        return 0

    if a.cmd == "verify":
        from lumen.record import load_record, verify_record

        check = verify_record(load_record(a.record))
        print(f"signature valid:            {check.signature_ok}")
        print(f"emission matches the nonce: {check.emission_matches_nonce}")
        for problem in check.problems:
            print(f"  - {problem}")
        return 0 if check.ok else 1

    if a.cmd == "reanalyse":
        from lumen.decide import decide, load_thresholds
        from lumen.record import load_record
        from lumen.session import SessionConfig, reanalyse

        record = load_record(a.record)
        f = reanalyse(record)
        extra = record.get("extra") or {}
        v = decide(f, load_thresholds(SessionConfig().thresholds_path),
                   face_fraction=extra.get("face_fraction", 1.0), pose=extra.get("pose_summary"))
        print(json.dumps({"features": f.to_dict() if f else None, "verdict": v.to_dict()}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
