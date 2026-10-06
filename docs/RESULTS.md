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

## 2026-10-05 — M1 genuine session G3, and the first calibration

G3: same place and settings, session `20261005-175335-G3-5210` (video saved), whole 60 s, no gaps.

M1 report (`experiments/m1_probe_survival/report.py --genuine G1 G2 G3`), each genuine session
judged by thresholds calibrated from the other two (leave-one-out):

| Session | Face coh. | Neck coh. | Floor (K) | Gain | Cheek contrast ± SE | Lag | Verdict (leave-one-out) |
|---|---|---|---|---|---|---|---|
| G1 | 0.864 | 0.866 | 0.074 (54) | 0.486 | +0.589 ± 0.029 | 133 ms | verify |
| G2 | 0.870 | 0.899 | 0.069 (58) | 0.508 | +0.573 ± 0.036 | 100 ms | verify |
| G3 | 0.876 | 0.893 | 0.069 (58) | 0.502 | +0.544 ± 0.027 | 100 ms | verify |

Calibration from all three (`data/thresholds.json`): cheek contrast ≥ 0.5003 (mean − 3 sd),
face/neck coherence ratio ≥ 0.774, gain in [0.340, 0.711], background/neck ≤ 0.30, |yaw| ≤ 0.088,
face/neck lightness ≤ 0.997, mirror sign +1.

Limits: three sessions of one subject in one sitting; an sd from n = 3 is rough, and with two
sessions the leave-one-out thresholds fall back to 0.8 x the minimum. The calibrated L3 bound
(0.50) sits only ~0.04 below the lowest genuine value, so more genuine sessions (other subjects,
small head movements) are needed before treating its false-reject rate as known. No attack has
been measured yet.

## 2026-10-05 — M1 replay attack R1

G1's lossless recording played back as the camera (`file:` source, released at its recorded
frame times, 19.3 fps) while a new one-time split probe ran full screen on the same laptop in the
same auditorium; no one in front of the camera. Calibrated thresholds from G1–G3 in force.
Session `20261005-175913-R1-d74a`; the video ran out at 58 s, longest gap-free stretch 53.3 s.

| Neck coh. (today's probe) / floor (K) | Face coh. | Face–neck coh. | Cheek contrast ± SE | Verdict |
|---|---|---|---|---|
| **0.094** / 0.078 (51) | — | **0.867** | +0.057 ± 0.065 | **reject** (replay rule) |

Reading: the recorded face and neck still follow each other (0.867, as in G1) but not today's
probe (neck 0.094, at the noise floor vs 0.87–0.90 genuine), which the SNR gate classifies as a
replay rather than an abstain. One replay of one recording: the replay rule's threshold
(face–neck ≥ 0.5) has a wide margin here, but it is still a provisional default, not calibrated.

## 2026-10-06 — Two genuine sessions in another room, judged by the auditorium calibration

Started from the console (45 s, split, amplitude 24), genuine subject, **no lamp**; room, light
and distance not recorded (the subject sat farther back in the second). Thresholds: the
auditorium calibration from G1–G3. Sessions `20261006-022130-session-e6e9`,
`20261006-022307-session-abb5`.

| Session | Face / neck coh. (floor 0.093) | Face/neck lightness (limit 0.997) | Eye distance | Cheek contrast | Verdict |
|---|---|---|---|---|---|
| e6e9 | 0.873 / 0.895 | **1.164** | 29.7 px | +0.503 | abstain (lamp gate) |
| abb5 | 0.617 / 0.667 | **1.277** | **21.8 px** | **+0.265** | abstain (lamp gate) |

Reading:

- **False abstain from an untransferred calibration.** In this room the face is naturally
  brighter than the neck (1.16–1.28) where in the auditorium it was darker (0.95–0.97), so the
  lamp gate fired with no lamp. As PROJECT.md says, calibration does not transfer between rooms:
  recalibrate in the room where verdicts are made.
- **Distance changes the cheek lean.** Sitting farther back (eye distance 30 → 22 px) roughly
  halved the cheek contrast (+0.50 → +0.27); with the lamp gate out of the way L3 would have
  falsely rejected a genuine subject. Added a distance gate: calibration stores 0.85 x the
  smallest calibration face size (eye distance), and a smaller face abstains ("sit closer").
  Two sessions, one subject; the size of the effect needs more distances to pin down.

### Calibration change (2026-10-06)

The L3 bound was mean − 3 sd over genuine sessions. With n = 3 that sd (0.023 for G1–G3) is small
and unreliable, and it ignores each session's own noise (jackknife SE ≈ 0.03); in a simulated
check a genuine session (+0.54) fell below such a bound (0.575). The bound is now
mean − 3 x sqrt(sd² + mean SE²). For G1–G3 it moves from 0.500 to **0.453**. The thresholds
recorded with the sessions above were computed with the old rule; recalibrate to apply the new
one. No session's measured values change.

## 2026-10-06 — First call check through Google Meet: INVALID (probe never reached the subject)

Verifier laptop in call mode (`--emit virtualcam`, amplitude 32, split, 30 s), subject on a second
laptop in a Google Meet call, subject's tile read from the verifier's screen
(`screen:74,88,1763,882`, tile updating at ~20 fps). Session `20261006-123333-CALL-SMOKE-2560`.

- Neck coherence 0.183, face 0.119, noise floor 0.154 (K = 26); background/neck gain 0.61;
  verdict abstain ("probe not detected on the neck reference").
- Afterwards the subject reported that **neither the grey warm-up nor the flicker appeared on
  their screen**: Meet on the verifier laptop was not showing the OBS Virtual Camera feed. The
  numbers are what no probe looks like (neck ≈ floor, background ≈ neck). Not evidence about the
  probe through a codec; kept as a procedure failure.
- Procedure fix: before any call session, send a visible test pattern into the virtual camera,
  select "OBS Virtual Camera" in Meet while it runs, and have the subject confirm they see it.

## 2026-10-06 — Codec survival on real recordings (offline re-encode, not a live call)

G1–G3's lossless 640x480 recordings re-encoded through PyAV (libx264 veryfast/zerolatency,
libvpx realtime cpu-used 8, 4:2:0), decoded and re-analysed with YuNet regions
(`experiments/codec_survival/run.py --session …`). Bitrates scaled from the 320x240 settings x4.

| Codec | Face coh. | Neck coh. | Cheek contrast ± SE |
|---|---|---|---|
| none | 0.83–0.88 | 0.83–0.90 | +0.547 … +0.582 (± 0.02–0.04) |
| H.264 2400 kbps | 0.29–0.41 | 0.65–0.72 | **+0.44 … +0.47** (± 0.04–0.09) |
| H.264 1200 kbps | 0.15–0.22 | 0.53–0.62 | +0.17 … +0.43 (± 0.08–0.16) |
| H.264 600 kbps | 0.12–0.20 | 0.42–0.50 | +0.24 … +0.39 (± 0.14–0.19) |
| H.264 320 kbps | 0.11–0.15 | 0.38–0.47 | +0.04 … +0.20 (± 0.04–0.21) |
| VP8 1200 kbps | 0.08–0.11 | 0.11–0.22 | −0.12 … +0.19 (± 0.12–0.21) |
| VP8 600 kbps | 0.08 | 0.10–0.13 | +0.07 … +0.15 (± 0.07–0.21) |

Floor 0.068–0.069 (K = 58).

Reading:

- Worse than the simulation predicted. Compression removes the probe from the **face far more
  than from the neck** (face 0.29–0.41 vs neck 0.65–0.72 at 2.4 Mbps H.264), so the genuine L1
  face/neck ratio falls from ~1.0 to ~0.5: uncompressed calibration would falsely reject a
  genuine call. Calibration must go through the call.
- The cheek lean survives good H.264 (+0.44–0.47 at 2.4 Mbps) and degrades into noise at
  ≤ 1.2 Mbps; with these VP8 settings the probe barely survives at all.
- Offline encoder settings, not Meet's. Consequences for the live call test: maximise video
  quality (720p send/receive), probe amplitude raised to 32 for all call sessions, and expect
  abstains rather than verdicts if the call's bitrate is low.

## 2026-10-05 — Call-mode output: colour through the OBS virtual camera (no subject)

OBS Studio 32.2.2 installed; LUMEN's `VirtualCamDisplay` (pyvirtualcam, 1280x720, 30 fps) fed a
static split field and the virtual camera was read back as a consumer would (`camera:1`, DSHOW,
640x480).

| Half | Sent RGB | Received RGB | Lab error (L*, a*, b*) |
|---|---|---|---|
| left | 184, 158, 168 | 184.1, 159.2, 168.1 | +0.32, −0.46, +0.58 |
| right | 158, 178, 168 | 158.1, 180.2, 168.1 | +0.46, −0.93, +0.78 |

Reading: both halves arrive on the correct side (the virtual camera does not mirror) with a
constant offset under 1 Lab unit (green +1–2 code values, from the colour-format conversion). A
constant offset does not affect the probe measurement, which uses the modulation. Static colours
only: whether a call app's codec passes the 2–8 Hz modulation is the open question for the call
sessions.

## 2026-10-05 — M1 lamp test L1 (genuine subject, light on the face from the front)

Same place and settings; a phone torch held in front of the subject, slightly above eye level,
~30–50 cm away (as instructed; not measured), aimed at the face. Calibrated thresholds (G1–G3).
Session `20261005-181600-L1-lamp-front-3020`, whole 60 s, no gaps.

| Face coh. / neck coh. / floor | Ratio (L1 ≥ 0.774) | Gain (L2 0.340–0.711); background | Cheek contrast (L3 ≥ 0.500) | Face/neck lightness (≤ 0.997) | Verdict |
|---|---|---|---|---|---|
| 0.396 / 0.619 / 0.069 | **0.641** | 0.348; 0.355 | **+0.244 ± 0.124** | **1.083** | **abstain** (lamp gate) |

Reading: the torch diluted the probe on the face (face coherence 0.40 vs 0.86–0.88 genuine) and
flattened the cheek lean. L1 and L3 would both have **falsely rejected this genuine subject**;
the lamp gate (face lit more strongly relative to the neck than in any calibration session)
abstained first, as designed. One lamp position: a side lamp (L2) is the harder case still to
measure.
