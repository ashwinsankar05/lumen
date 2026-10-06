"""Is the neck-referenced lean (cheek contrast / neck contrast) steadier than the raw cheek contrast?

Re-measures every saved session video (sessions recorded with --save-video) with the two neck
halves added, and prints per session: cheek contrast, neck contrast, their ratio (± jackknife SE).
Then the spread of each across sessions, and the L3-style bound mean - 3 sqrt(sd^2 + SE^2) as a
fraction of the mean: the lower that fraction, the closer a genuine-only threshold sits to
genuine, and the smaller a swap's drop it can catch.

    python experiments/deepcheck/neck_reference.py data/sessions [more session folders ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from lumen.deepcheck import lean_from_session


def bound_fraction(values, ses) -> float:
    v, se = np.asarray(values, float), np.asarray(ses, float)
    return float((v.mean() - 3 * np.sqrt(v.std(ddof=1) ** 2 + np.mean(se ** 2))) / v.mean())


def main(argv: list[str]) -> int:
    dirs = []
    for arg in argv:
        p = Path(arg)
        dirs += sorted(q for q in p.iterdir() if q.is_dir()) if not (p / "record.json").exists() else [p]
    rows = []
    for d in dirs:
        if not (d / "video.mkv").exists():
            continue
        rec = json.loads((d / "record.json").read_text())
        try:
            m = lean_from_session(d, rec)
        except ValueError as exc:  # e.g. the face was never found while the probe ran
            print(f"{d.name}: not measurable ({exc})")
            continue
        if m is None or m.lean_ratio is None:
            print(f"{d.name}: not measurable")
            continue
        rows.append((d.name, m))
        print(f"{d.name:36s} cheek {m.cheek_contrast:+.3f}  neck {m.neck_contrast:+.3f}  "
              f"ratio {m.lean_ratio:+.3f} ± {m.lean_ratio_se if m.lean_ratio_se is not None else float('nan'):.3f}")
    if len(rows) >= 3:
        cheek = [m.cheek_contrast for _, m in rows]
        ratio = [m.lean_ratio for _, m in rows]
        ratio_se = [m.lean_ratio_se or 0.0 for _, m in rows]
        print(f"\ncheek contrast: mean {np.mean(cheek):+.3f}, CV {np.std(cheek, ddof=1) / np.mean(cheek):.1%}")
        print(f"lean ratio:     mean {np.mean(ratio):+.3f}, CV {np.std(ratio, ddof=1) / np.mean(ratio):.1%}, "
              f"bound at {bound_fraction(ratio, ratio_se):.0%} of the mean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
