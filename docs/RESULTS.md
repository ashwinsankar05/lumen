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
