"""Features -> verify / reject / abstain, with every check, its value, threshold and reason.

Order of checks (the first that decides, decides):

  1. Capture and data quality: no capture error, enough probe time, no long gaps, face and neck
     found in most frames. Failing -> abstain.
  2. Head pose: turned further than in any calibration session -> abstain (the light geometry is
     outside what was measured; L3 in particular depends on it).
  3. SNR gate on the neck, the reference: it must follow today's probe well above the noise
     floor p/K. Failing -> reject if face and neck strongly follow *each other* (both respond to
     a colour signal that is not today's probe: a replay), else abstain (too much ambient light,
     subject too far, probe too small on their screen).
  4. Extra light on the face relative to the neck, beyond calibration (a lamp) -> abstain. A lamp
     dilutes the probe on the face only, which would otherwise look like a partial swap.
  5. L1 face/neck coherence ratio, L2 differential gain and background falloff, L3 cheek
     contrast (split probe, once calibrated). Any failure -> reject. All pass -> verify.

Thresholds come from genuine captures in the deployment's own conditions via `calibrate`, never
from deepfake examples (PROJECT.md §6). Until then the defaults below are placeholders, flagged
`provisional`, and every verdict says so.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

import numpy as np

from lumen.features import Features

Decision = Literal["verify", "reject", "abstain"]


@dataclass(frozen=True)
class Thresholds:
    provisional: bool = True
    source: str = "built-in placeholders: not derived from any capture"
    n_genuine: int = 0
    # 1. data quality
    min_duration_s: float = 20.0
    max_gap_s: float = 0.5
    min_face_fraction: float = 0.8
    # 3. SNR gate on the neck: max(min, multiple x noise floor)
    neck_gate_min: float = 0.15
    neck_gate_floor_multiple: float = 4.0
    replay_face_neck_coherence_min: float = 0.5
    # L1
    face_to_neck_coherence_min: float = 0.7
    # L2
    gain_range: tuple[float, float] = (0.4, 2.5)
    background_gain_ratio_max: float = 0.6
    # Enforced only once calibrated (None: not yet)
    cheek_contrast_min: float | None = None       # L3
    cheek_contrast_sign: int = 1                  # -1 when calibration saw mirrored video
    max_abs_yaw: float | None = None              # 2. pose gate
    face_neck_lightness_max: float | None = None  # 4. lamp gate

    def neck_gate(self, noise_floor: float) -> float:
        return max(self.neck_gate_min, self.neck_gate_floor_multiple * noise_floor)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Thresholds":
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        if "gain_range" in d:
            d["gain_range"] = tuple(d["gain_range"])
        return cls(**d)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Thresholds":
        return cls.from_dict(json.loads(Path(path).read_text()))


def load_thresholds(path: str | Path) -> Thresholds:
    """Calibrated thresholds if the file exists, else the provisional defaults."""
    return Thresholds.load(path) if Path(path).exists() else Thresholds()


@dataclass(frozen=True)
class Check:
    name: str
    value: float | None
    threshold: str
    passed: bool | None  # None: not evaluated


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str
    checks: list[Check] = field(default_factory=list)
    provisional: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def decide(
    features: Features | None,
    thresholds: Thresholds | None = None,
    *,
    face_fraction: float = 1.0,
    capture_error: str | None = None,
    pose: dict | None = None,
) -> Verdict:
    """The verdict for one session. `pose` is session.pose_summary (median yaw etc.) or None."""
    th = thresholds or Thresholds()
    checks: list[Check] = []

    def done(decision: Decision, reason: str) -> Verdict:
        return Verdict(decision, reason, checks, th.provisional)

    # 1. capture and data quality
    if capture_error:
        checks.append(Check("capture", None, "no capture error", False))
        return done("abstain", f"capture failed: {capture_error}")
    if features is None:
        return done("abstain", "not enough data to measure")
    m = features.measurement
    # Nyquist: below 2 x the top probe frequency the camera cannot sample the flicker at all.
    min_fps = 2.0 * m.band_hz[1]
    quality = [
        Check("capture_fps", m.capture_fps, f">= {min_fps:g} (2 x top probe frequency)", m.capture_fps >= min_fps),
        Check("duration_s", m.duration_s, f">= {th.min_duration_s}", m.duration_s >= th.min_duration_s),
        Check("max_gap_s", m.max_gap_s, f"<= {th.max_gap_s}", m.max_gap_s <= th.max_gap_s),
        Check("face_fraction", face_fraction, f">= {th.min_face_fraction}", face_fraction >= th.min_face_fraction),
    ]
    checks += quality
    if failed := [c.name for c in quality if not c.passed]:
        reason = "data quality too low: " + ", ".join(failed)
        if "capture_fps" in failed:
            reason += (f" (camera delivered {m.capture_fps:.1f} fps; in dim light webcams lengthen exposure "
                       "and slow down, so add light or raise screen brightness)")
        return done("abstain", reason)

    # 2. head pose
    if pose is not None and th.max_abs_yaw is not None:
        ok = abs(pose["yaw"]) <= th.max_abs_yaw
        checks.append(Check("head_yaw", pose["yaw"], f"|yaw| <= {th.max_abs_yaw}", ok))
        if not ok:
            return done("abstain", "head turned further than in any calibration session: the light "
                                   "geometry is outside what was measured")

    # 3. SNR gate on the neck reference, with the replay test when it fails
    gate = th.neck_gate(m.noise_floor)
    neck_ok = m.neck.coherence >= gate
    checks.append(Check("neck_coherence", m.neck.coherence,
                        f">= {gate:.3f} (max of {th.neck_gate_min} and {th.neck_gate_floor_multiple} x "
                        f"floor {m.noise_floor:.3f}, K = {m.n_segments})", neck_ok))
    if not neck_ok:
        replay = features.face_neck_coherence >= th.replay_face_neck_coherence_min
        checks.append(Check("face_neck_coherence", features.face_neck_coherence,
                            f"< {th.replay_face_neck_coherence_min} (else replay)", not replay))
        if replay:
            return done("reject", "face and neck follow a colour signal that is not today's probe "
                                  "(consistent with a replayed session)")
        return done("abstain", "probe not detected on the neck reference: too much ambient light, "
                               "subject too far, or the probe too small on their screen")

    # 4. lamp gate
    if th.face_neck_lightness_max is not None and features.face_neck_lightness is not None:
        ok = features.face_neck_lightness <= th.face_neck_lightness_max
        checks.append(Check("face_neck_lightness", features.face_neck_lightness,
                            f"<= {th.face_neck_lightness_max}", ok))
        if not ok:
            return done("abstain", "the face is lit more strongly relative to the neck than in any "
                                   "calibration session (a lamp near the face?): the probe is diluted")

    # 5. the three layers
    ratio = m.face.coherence / m.neck.coherence
    l1 = ratio >= th.face_to_neck_coherence_min
    checks.append(Check("L1_face_to_neck_coherence", ratio, f">= {th.face_to_neck_coherence_min}", l1))

    lo, hi = th.gain_range
    l2_gain = lo <= m.differential_gain <= hi
    checks.append(Check("L2_differential_gain", m.differential_gain, f"in [{lo}, {hi}]", l2_gain))

    bg = features.background_gain_ratio
    l2_falloff = None if bg is None else bg <= th.background_gain_ratio_max
    checks.append(Check("L2_background_falloff", bg, f"<= {th.background_gain_ratio_max}", l2_falloff))

    contrast = features.cheek_contrast
    l3 = None
    if contrast is None:
        checks.append(Check("L3_cheek_contrast", None, "needs the two-sided probe", None))
    elif th.cheek_contrast_min is None:
        checks.append(Check("L3_cheek_contrast", contrast, "needs calibration (genuine two-sided sessions)", None))
    else:
        oriented = th.cheek_contrast_sign * contrast
        l3 = oriented >= th.cheek_contrast_min
        mirrored = " (video mirrored: sign flipped)" if th.cheek_contrast_sign < 0 else ""
        checks.append(Check("L3_cheek_contrast", oriented, f">= {th.cheek_contrast_min}{mirrored}", l3))

    if not l1:
        return done("reject", "the face does not follow the probe while the neck does (L1)")
    if not l2_gain:
        return done("reject", "face/neck response ratio outside the physically plausible range (L2)")
    if l2_falloff is False:
        return done("reject", "the background responds as strongly as the subject: no distance falloff (L2)")
    if l3 is False:
        return done("reject", "the cheeks do not lean towards the halves of the screen they face "
                              "as a real 3D face does (L3)")
    reason = "face and neck respond to today's probe together, in plausible proportions"
    if l3 is True:
        reason += ", and the cheeks lean towards the halves they face"
    else:
        reason += " (L3 not evaluated)"
    return done("verify", reason)


def calibrate(genuine: list[Features], base: Thresholds | None = None,
              poses: list[dict | None] | None = None) -> Thresholds:
    """Thresholds from genuine sessions recorded in the deployment's own conditions.

    Bounds sit outside the observed genuine spread with a margin. L3 uses mean - 3 sd once three
    or more two-sided sessions exist (else 0.8 x the minimum). Fewer than three sessions keeps
    the result flagged provisional.
    """
    if not genuine:
        raise ValueError("need at least one genuine session")
    base = base or Thresholds()
    ms = [f.measurement for f in genuine]
    ratios = [m.face.coherence / m.neck.coherence for m in ms]
    gains = [m.differential_gain for m in ms]

    updates: dict = dict(
        provisional=len(genuine) < 3,
        source=f"calibrated from {len(genuine)} genuine session(s)",
        n_genuine=len(genuine),
        face_to_neck_coherence_min=round(0.8 * min(ratios), 4),
        gain_range=(round(0.7 * min(gains), 4), round(1.4 * max(gains), 4)),
    )
    if bgs := [f.background_gain_ratio for f in genuine if f.background_gain_ratio is not None]:
        updates["background_gain_ratio_max"] = round(max(0.3, 1.5 * max(bgs)), 4)

    # A call app that mirrors the video swaps left and right, flipping the sign of every genuine
    # contrast. Learn the orientation from the genuine sessions; a swap sits near 0 either way.
    contrasts = [f.cheek_contrast for f in genuine if f.cheek_contrast is not None]
    if contrasts:
        sign = -1 if np.mean(contrasts) < 0 else 1
        updates["cheek_contrast_sign"] = sign
        contrasts = [sign * c for c in contrasts]
    if len(contrasts) >= 3:
        updates["cheek_contrast_min"] = round(max(0.0, float(np.mean(contrasts) - 3 * np.std(contrasts, ddof=1))), 4)
    elif contrasts:
        updates["cheek_contrast_min"] = round(max(0.0, 0.8 * min(contrasts)), 4)

    measured = [p for p in (poses or []) if p]
    if len(measured) >= 3:
        spread = max(p.get("yaw_iqr", 0.0) for p in measured)
        updates["max_abs_yaw"] = round(max(abs(p["yaw"]) for p in measured) + spread + 0.03, 4)

    lightness = [f.face_neck_lightness for f in genuine if f.face_neck_lightness is not None]
    if len(lightness) >= 3:
        updates["face_neck_lightness_max"] = round(max(lightness) + 0.03, 4)

    return replace(base, **updates)
