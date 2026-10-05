# Measured results

Measured results only. Every entry carries its date, hardware and conditions (room light,
subject distance, display brightness, camera settings and whether exposure/white balance were
locked), the session ids, and the analysis settings (band, segment length, K). Simulated numbers
never go here (PROJECT.md §4). Results that fail to reproduce are recorded here too, not retuned
away (§12).

Template for an entry:

```
## YYYY-MM-DD — <what was measured>

- Hardware: <laptop, camera index and backend, resolution, measured fps, display, brightness>
- Conditions: <light sources, time of day, distance, pose, probe layout and amplitude, duration>
- Camera: exposure locked? white balance locked? (from the capture report)
- Sessions: <ids and labels>

| Session | Face coh. | Neck coh. | Floor (K) | Gain | Cheek contrast | Lag | Verdict |
|---|---|---|---|---|---|---|---|

Reading: <what the numbers do and do not show; limits>
```

---

## 2026-10-05 — First webcam smoke test (not M1)

- Hardware: development laptop, built-in webcam `camera:0` (DirectShow, 640x480 requested at
  30 fps). Probe in a **1280x720 window, not full screen** (`--windowed`), split layout,
  amplitude 24, 20 s after a 6 s warm-up.
- Conditions **not recorded**: room light, distance, display brightness.
- Session: `20261005-173334-smoke-23f7`.

| Measured | Value |
|---|---|
| Camera frame rate | **10.0 fps** (requested 30) |
| Face + neck found | 100% of probe-period frames |
| Processing | 5.0 ms/frame |
| Probe frames shown | 1185 in 20 s (59 fps) |
| Usable probe time | 13.9 s (camera lock ran until 5.9 s into the probe) |
| Exposure / WB locked | no (closest manual exposure 44.5 L* too dark) / yes (2800 K) |
| Face coh. / neck coh. / floor (K) | not interpretable: lag at the 1 s search limit, K = 12 |
| Cheek contrast | +0.002 ± 0.049 (no signal) |
| Verdict | abstain (duration) |

Diagnosis (camera only, no probe):

- The lock's exposure search ended on a 125 ms manual exposure (2^-3 s) and the driver kept it,
  even with auto exposure switched back on: 10–11 fps afterwards.
- Manual exposures up to 31 ms give 30 fps but near-black frames (mean brightness 2–5 of 255),
  so no manual exposure can match the auto image in this room.
- Auto exposure, re-enabled from a short exposure: 16–17.5 fps at normal brightness. Below
  16 fps the camera cannot sample an 8 Hz probe component at all.

Fixes in code: exposure search limited to one frame period, auto exposure always re-enabled from
a short exposure, warm-up 8 s with the lock at 0.5 s, and an abstain on capture fps below
2 x the top probe frequency. Next: repeat full screen at 100% brightness with room lights on.

## 2026-10-05 — Full-screen smoke test after the camera fixes (not M1)

- Hardware: as above. Probe **full screen**, display brightness 100%, room lights on, subject
  about 40 cm away facing the screen (as instructed; not measured). Split layout, amplitude 24,
  8 s warm-up, 20 s probe, 2 s Welch segments, band 2–8 Hz.
- Session: `20261005-174148-smoke-full-b061` (record verifies; numbers from re-analysis).

| Measured | Value |
|---|---|
| Camera frame rate | 19.9 fps (requested 30); 0 dropped; processing 4.7 ms/frame |
| Face + neck found | 100% |
| Exposure / WB locked | no (manual exposure 53.5 L* too dark) / yes; lock done before the probe |
| Lag (from the neck) | 133 ms |
| Face coh. / neck coh. / floor (K) | **0.692 / 0.569 / 0.222 (18)** |
| Face/neck gain; background/neck gain | 0.596; 0.517 |
| Lateral index: image-right cheek / image-left cheek | +0.153 / −0.071 |
| Cheek contrast ± jackknife SE | +0.224 ± 0.115 |
| Verdict | abstain (19.87 s analysed < 20 s minimum: the smoke default was too short) |

Reading: the probe is detected on a real face and neck at ~3x the noise floor, and the cheeks
lean in opposite directions with the predicted sign. One session of 20 s: the cheek contrast is
only ~2 SE from zero, and the background responds at half the neck's gain (wall distance not
recorded). Not evidence about attacks. Next: 60 s genuine sessions (floor ~0.07), wall distance
written down. Smoke default raised to 30 s.

## 2026-10-05 — M1 genuine session G1

- Hardware: as above; camera 19.9–20.8 fps, exposure on auto (not lockable), white balance locked.
  Probe full screen, brightness 100% (as instructed), split layout, amplitude 24, 60 s, 8 s
  warm-up, 2 s segments, band 2–8 Hz.
- Place: an auditorium; the wall behind the subject far away (subject's report). Subject facing
  the screen at ~40 cm (as instructed; not measured). Inter-eye distance ~64 px at 640x480.
- Session: `20261005-174457-G1-f347`, lossless video saved (replay and codec runs).

| | Whole 60 s (at recording) | Longest gap-free stretch (re-analysis) |
|---|---|---|
| Analysed | 59.8 s, K = 58 | 0.0–55.3 s, K = 54 |
| Face coh. / neck coh. / floor | 0.833 / 0.828 / 0.069 | **0.864 / 0.866 / 0.074** |
| Face/neck coherence ratio | 1.006 | 0.998 |
| Face/neck gain; background/neck gain | 0.489; **0.082** | — |
| Cheek contrast ± SE (right +0.366, left −0.216) | +0.582 ± 0.023 | **+0.589 ± 0.029** |
| Lag | 133 ms | 133 ms |
| Max gap | 1.105 s | 0.067 s |
| Verdict (provisional thresholds) | abstain (max_gap_s) | verify (L3 not yet calibrated) |

- The face was lost twice near the end (55.4–56.1 s, 57.0–58.0 s; head yaw up to 0.31, i.e. the
  subject turned). The recorded verdict was abstain; analysis was then changed to use the longest
  gap-free stretch rather than interpolate across a > 0.5 s gap, and the re-analysis verifies.
  Both are kept here; the change was made because interpolating over a look-away invents data,
  not to make G1 pass.
- Reading: on a real face in a large room the probe is ~12x the noise floor on face and neck,
  the far background barely responds (0.08 of the neck), and the cheeks lean in opposite
  directions with the predicted sign, ~20 SE from zero. One subject, one session: this is the
  genuine baseline the attack sessions will be compared with, not a result about attacks.

## 2026-10-05 — M1 genuine session G2

Same place, hardware and settings as G1, a few minutes later. Session `20261005-175133-G2-33fb`
(video saved). Whole 60 s analysed, no gaps (face found in 100% of frames), head yaw −0.02
(IQR 0.04), 20.8 fps.

| Face coh. / neck coh. / floor (K) | Ratio | Gain; background | Cheek contrast ± SE (right / left) | Lag | Verdict (provisional) |
|---|---|---|---|---|---|
| 0.870 / 0.899 / 0.069 (58) | 0.967 | 0.508; 0.060 | **+0.573 ± 0.036** (+0.373 / −0.199) | 100 ms | verify (L3 uncalibrated) |

Reading: G1 and G2 agree (cheek contrast 0.589 vs 0.573, within one SE of each other; face and
neck coherence 0.86–0.90). Same subject, same pose, same room, minutes apart: repeatability
within one sitting, not across subjects or rooms.
