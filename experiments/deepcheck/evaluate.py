"""Reproduce the deep-check results (docs/RESULTS.md) from saved records.

Held-out test: calibrate on some genuine sessions, judge others the calibration never saw.
    python experiments/deepcheck/evaluate.py --calibrate CF1 CF2 CF3 --judge CS1 CF4 CS2 CF5

Leave-one-out over a pool of genuine sessions (each judged by a calibration from all the others),
plus the attack sessions judged by a calibration from the whole pool:
    python experiments/deepcheck/evaluate.py --loo G1 G2 ... --attacks CS1 CS2

Arguments are session folders or record files. Only real captures are accepted: a simulated
record is refused, so a simulated number cannot end up in a results table (PROJECT.md §4).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lumen.deepcheck import _load, calibrate_deep, deep_measures, judge


def measure_all(paths: list[str]) -> list:
    out = []
    for p in paths:
        m = deep_measures(_load(p))
        if m is not None and m.simulated:
            raise SystemExit(f"{p}: simulated record, refused")
        out.append((Path(p).name, m))
    return out


def row(name, m, th) -> str:
    v = judge(m, th)
    if m is None or m.cheek_contrast is None or m.relief is None:
        return f"| {name} | – | – | {v.decision} |"
    return (f"| {name} | {th.contrast_sign * m.cheek_contrast:+.3f} | {m.relief:.3f} | "
            f"{v.decision}{' (lean low)' if v.lean_low and not v.relief_low else ''}"
            f"{' (relief low)' if v.relief_low and not v.lean_low else ''} |")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calibrate", nargs="*", default=[])
    p.add_argument("--judge", nargs="*", default=[])
    p.add_argument("--loo", nargs="*", default=[], help="genuine pool for leave-one-out")
    p.add_argument("--attacks", nargs="*", default=[], help="with --loo: attack sessions")
    p.add_argument("--k", type=float, default=2.0)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    header = "| Session | Lean | Relief | Deep verdict |\n|---|---|---|---|"

    if a.calibrate:
        cal = measure_all(a.calibrate)
        th = calibrate_deep([m for _, m in cal], k=a.k)
        print(f"Calibrated on {th.n_genuine}: lean >= {th.contrast_min}, relief >= {th.relief_min} (k = {th.k})\n")
        print(header)
        for name, m in measure_all(a.judge):
            print(row(name, m, th))

    if a.loo:
        pool = measure_all(a.loo)
        print(f"\nLeave-one-out over {len(pool)} genuine sessions (k = {a.k}):\n")
        print(header)
        false_alarms = judged = 0
        for i, (name, m) in enumerate(pool):
            th = calibrate_deep([x for j, (_, x) in enumerate(pool) if j != i], k=a.k)
            v = judge(m, th)
            judged += v.decision != "abstain"
            false_alarms += v.decision == "swap"
            print(row(name, m, th))
        print(f"\nFalse alarms: {false_alarms} of {judged} judged genuine sessions.")
        if a.attacks:
            th = calibrate_deep([m for _, m in pool], k=a.k)
            print(f"\nAttacks, calibrated on the whole pool (lean >= {th.contrast_min}, relief >= {th.relief_min}):\n")
            print(header)
            for name, m in measure_all(a.attacks):
                print(row(name, m, th))
    return 0


if __name__ == "__main__":
    sys.exit(main())
