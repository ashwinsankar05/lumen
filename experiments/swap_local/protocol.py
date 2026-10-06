"""Guided local face-swap test: the swap on and off through the SAME video path, no video call.

Run on the attacker's laptop (the one running FaceFusion + OBS):

    python experiments/swap_local/protocol.py --camera <OBS Virtual Camera index>

The probe fills that laptop's screen and lights the subject directly; LUMEN reads the FaceFusion
output through the OBS Virtual Camera. Every session goes through the same path, only FaceFusion's
face swap is toggled, so a difference between swap-off (CF) and swap-on (CS) sessions can only come
from the swap (PROJECT.md §8: genuine baseline in the same session and light).

Sequence: CF1 CF2 CF3 (swap off) -> calibrate from them -> CS1 (on) CF4 (off) CS2 (on) CF5 (off).
Before every session the operator confirms the subject is ready (PROJECT.md §12).
Results: a table on screen, results/<timestamp>/results.md, and the signed records in data/sessions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from lumen.decide import calibrate, decide, load_thresholds
from lumen.record import load_record
from lumen.session import Session, SessionConfig, reanalyse, samples_from_record

HERE = Path(__file__).resolve().parent
SEQUENCE = [
    ("CF1", False), ("CF2", False), ("CF3", False),
    ("CS1", True), ("CF4", False), ("CS2", True), ("CF5", False),
]
CALIBRATE_AFTER = "CF3"


def video_rate(record: dict) -> float:
    """Rate at which the source actually changed: frozen frames (e.g. a hidden browser preview
    that stopped repainting) repeat the same neck colour exactly."""
    t, samples = samples_from_record(record)
    neck = np.array([s["neck"] for s in samples if s and "neck" in s], dtype=float)
    if len(neck) < 3:
        return 0.0
    changed = np.abs(np.diff(neck, axis=0)).sum(axis=1) > 1e-6
    fps = (len(t) - 1) / (t[-1] - t[0]) if len(t) > 1 else 0.0
    return float(fps * changed.mean())


def summary_row(label: str, swap: bool, record_path: str, thresholds_path: str) -> dict:
    rec = load_record(record_path)
    f = reanalyse(rec)
    extra = rec.get("extra") or {}
    v = decide(f, load_thresholds(thresholds_path), face_fraction=extra.get("face_fraction", 1.0),
               pose=extra.get("pose_summary"))
    m = f.measurement if f else None
    lat = f.lateral if f else {}
    return {
        "label": label, "swap": swap, "session": rec["session_id"],
        "video_fps": round(video_rate(rec), 1),
        "face_coherence": m and round(m.face.coherence, 3),
        "neck_coherence": m and round(m.neck.coherence, 3),
        "noise_floor": m and round(m.noise_floor, 3),
        "cheek_right": round(lat["face.cheek_r"], 3) if "face.cheek_r" in lat else None,
        "cheek_left": round(lat["face.cheek_l"], 3) if "face.cheek_l" in lat else None,
        "cheek_contrast": None if not f or f.cheek_contrast is None else round(f.cheek_contrast, 3),
        "cheek_contrast_se": None if not f or f.cheek_contrast_se is None else round(f.cheek_contrast_se, 3),
        "verdict": v.decision, "reason": v.reason,
    }


def markdown(rows: list[dict]) -> str:
    def v(x):
        return "–" if x is None else x

    lines = ["| Session | Swap | Video fps | Face / neck coh. (floor) | Cheek R / L | Cheek contrast ± SE | Verdict |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        contrast = "–" if r["cheek_contrast"] is None else f"{r['cheek_contrast']:+.3f} ± {v(r['cheek_contrast_se'])}"
        lines.append(f"| {r['label']} | {'ON' if r['swap'] else 'off'} | {r['video_fps']} | "
                     f"{v(r['face_coherence'])} / {v(r['neck_coherence'])} ({v(r['noise_floor'])}) | "
                     f"{v(r['cheek_right'])} / {v(r['cheek_left'])} | {contrast} | {r['verdict']} |")
    return "\n".join(lines) + "\n"


def contrast_summary(rows: list[dict]) -> str:
    on = [r["cheek_contrast"] for r in rows if r["swap"] and r["cheek_contrast"] is not None]
    off = [r["cheek_contrast"] for r in rows if not r["swap"] and r["cheek_contrast"] is not None]
    if not on or not off:
        return "Not enough sessions for a swap-on vs swap-off comparison."
    return (f"Cheek contrast, swap OFF: {min(off):+.3f} to {max(off):+.3f} (n = {len(off)}); "
            f"swap ON: {min(on):+.3f} to {max(on):+.3f} (n = {len(on)}). "
            + ("No overlap: every swap session below every genuine one."
               if max(on) < min(off) else "The ranges overlap."))


def ask(prompt: str) -> bool:
    return input(prompt).strip().lower() in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", type=int, required=True, help="index of OBS Virtual Camera (python -m lumen cameras)")
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--amplitude", type=float, default=32.0)
    p.add_argument("--thresholds", default="data/thresholds-local-swap.json")
    p.add_argument("--start-at", default="CF1", help="resume from this label")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    labels = [s[0] for s in SEQUENCE]
    if a.start_at not in labels:
        p.error(f"--start-at must be one of {labels}")
    done_file = Path("data") / "swap_local_sessions.json"
    done: dict = json.loads(done_file.read_text()) if done_file.exists() else {}

    print(__doc__.split("\n\n")[1])
    for label, swap in SEQUENCE[labels.index(a.start_at):]:
        print("\n" + "=" * 70)
        print(f"  {label}: FaceFusion face swap {'ON  (the attack)' if swap else 'OFF (real face through the same path)'}")
        print("  The subject: same seat, ~40 cm, facing the screen, still for the whole session.")
        print(f"  Duration: 8 s grey + {a.duration:g} s colour flicker, full screen. Esc stops it.")
        if not ask(f"  Swap is {'ON' if swap else 'OFF'} and the subject is ready? (yes/no): "):
            print("  Stopped. Resume later with --start-at", label)
            break
        cfg = SessionConfig(source=f"camera:{a.camera}", duration_s=a.duration, amplitude=a.amplitude,
                            label=label, lock_camera=False, thresholds_path=a.thresholds)
        result = Session(cfg).run()
        done[label] = {"swap": swap, "record": result.record_path}
        done_file.parent.mkdir(parents=True, exist_ok=True)
        done_file.write_text(json.dumps(done, indent=2))
        f = result.features
        contrast = "–" if not f or f.cheek_contrast is None else f"{f.cheek_contrast:+.3f}"
        print(f"  -> {result.verdict.decision.upper()}: {result.verdict.reason}")
        print(f"     cheek contrast {contrast}, face found {result.face_fraction:.0%}")

        if label == CALIBRATE_AFTER:
            genuine = [done[k] for k in ("CF1", "CF2", "CF3") if k in done and done[k]["record"]]
            recs = [load_record(g["record"]) for g in genuine]
            feats = [reanalyse(r) for r in recs]
            if len(feats) < 3 or any(x is None for x in feats):
                print("  Calibration needs three usable swap-off sessions; repeat one with --start-at.")
                break
            th = calibrate(feats, poses=[(r.get("extra") or {}).get("pose_summary") for r in recs])
            th.save(a.thresholds)
            print(f"  Calibrated from CF1-CF3: cheek contrast must be >= {th.cheek_contrast_min} -> {a.thresholds}")

    rows = [summary_row(k, v["swap"], v["record"], a.thresholds) for k, v in done.items() if v.get("record")]
    if rows:
        table = markdown(rows)
        print("\n" + table + "\n" + contrast_summary(rows))
        out = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.md").write_text(table + "\n" + contrast_summary(rows) + "\n", encoding="utf-8")
        (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nSaved to {out}. Bring back the whole data folder and this results folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
