"""Milestone 1 report: re-analyse session records and tabulate what M1 needs (docs/PROTOCOL.md).

    python experiments/m1_probe_survival/report.py ID [ID ...] [--genuine ID ...]

Every session is re-analysed from its signed record (signature and emission checked first).
Sessions given with --genuine are used for calibration; each genuine session is judged by
thresholds calibrated *without* it (leave-one-out), the others by thresholds from all genuine.
Output: a Markdown table on stdout and results/<timestamp>/{results.md, results.json}.
Simulated records are flagged in every row; they never belong in docs/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from lumen.decide import Thresholds, calibrate, decide
from lumen.record import load_record, verify_record
from lumen.session import SessionConfig, reanalyse

HERE = Path(__file__).resolve().parent


def load(session_id: str, out_dir: Path) -> dict:
    rec = load_record(out_dir / session_id / "record.json")
    check = verify_record(rec)
    if not check.ok:
        raise SystemExit(f"{session_id}: record does not verify: {'; '.join(check.problems)}")
    return rec


def rows_for(records: dict[str, dict], genuine: list[str]) -> list[dict]:
    feats = {sid: reanalyse(rec) for sid, rec in records.items()}
    poses = {sid: (rec.get("extra") or {}).get("pose_summary") for sid, rec in records.items()}
    usable = [g for g in genuine if feats.get(g) is not None]

    def thresholds(excluding: str | None) -> Thresholds:
        ids = [g for g in usable if g != excluding]
        return calibrate([feats[g] for g in ids], poses=[poses[g] for g in ids]) if ids else Thresholds()

    rows = []
    for sid, rec in records.items():
        f = feats[sid]
        extra = rec.get("extra") or {}
        th = thresholds(sid if sid in usable else None)
        v = decide(f, th, face_fraction=extra.get("face_fraction", 1.0), pose=poses[sid])
        m = f.measurement if f else None
        rows.append({
            "session": sid,
            "label": extra.get("label", ""),
            "simulated": bool((rec.get("capture") or {}).get("simulated")),
            "layout": rec["probe"]["layout"],
            "genuine": sid in genuine,
            "face_coherence": m and round(m.face.coherence, 3),
            "neck_coherence": m and round(m.neck.coherence, 3),
            "noise_floor": m and round(m.noise_floor, 3),
            "K": m and m.n_segments,
            "gain": m and round(m.differential_gain, 3),
            "cheek_contrast": None if f is None or f.cheek_contrast is None else round(f.cheek_contrast, 3),
            "cheek_contrast_se": None if f is None or f.cheek_contrast_se is None else round(f.cheek_contrast_se, 3),
            "lag_ms": m and round(1000 * m.lag_s),
            "decision": v.decision,
            "reason": v.reason,
            "thresholds": "leave-one-out" if sid in usable else f"{len(usable)} genuine" if usable else "provisional",
        })
    return rows


def contrast(row: dict) -> str:
    """Cheek contrast with its within-session jackknife SE, e.g. '+0.452 ± 0.031'."""
    if row["cheek_contrast"] is None:
        return "–"
    se = "" if row["cheek_contrast_se"] is None else f" ± {row['cheek_contrast_se']:.3f}"
    return f"{row['cheek_contrast']:+.3f}{se}"


def markdown(rows: list[dict]) -> str:
    head = ("| Session | Label | Probe | Face coh. | Neck coh. | Floor (K) | Gain | Cheek contrast ± SE | Lag | "
            "Verdict | Thresholds |\n|---|---|---|---|---|---|---|---|---|---|---|\n")
    fmt = lambda v: "–" if v is None else v  # noqa: E731
    lines = []
    for r in rows:
        label = r["label"] + (" (SIMULATED)" if r["simulated"] else "") + (" [genuine]" if r["genuine"] else "")
        lines.append(f"| `{r['session']}` | {label} | {r['layout']} | {fmt(r['face_coherence'])} | "
                     f"{fmt(r['neck_coherence'])} | {fmt(r['noise_floor'])} ({fmt(r['K'])}) | {fmt(r['gain'])} | "
                     f"{contrast(r)} | {fmt(r['lag_ms'])} ms | {r['decision']} | {r['thresholds']} |")
    return head + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sessions", nargs="*", help="session ids to report")
    p.add_argument("--genuine", nargs="*", default=[], help="genuine session ids (calibration)")
    p.add_argument("--out-dir", default=SessionConfig().out_dir)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the table uses ± and –

    ids = list(dict.fromkeys(a.genuine + a.sessions))
    if not ids:
        p.error("give at least one session id")
    records = {sid: load(sid, Path(a.out_dir)) for sid in ids}
    rows = rows_for(records, a.genuine)
    table = markdown(rows)
    print(table)

    out = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.md").write_text(table, encoding="utf-8")
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"written to {out}")
    if any(r["simulated"] for r in rows):
        print("WARNING: simulated sessions included; do not copy these numbers into docs/RESULTS.md",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
