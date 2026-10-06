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

## 2026-10-06 — Live face swap through a Google Meet call (FaceFusion)

Verifier laptop in call mode (split probe, amplitude 34, 90–120 s, `--emit virtualcam`), subject
on a second laptop in a Meet call, tile read at ~30 fps from the verifier's screen
(`screen:74,88,1763,882`). Attack: FaceFusion live swap (webcam → FaceFusion → OBS window capture
→ OBS Virtual Camera → Meet), source face a consenting person's photo. Genuine "Real" path:
webcam straight to Meet. Call calibration from CG1–CG3 (`data/thresholds-call.json`: cheek
contrast ≥ 0.329, face/neck ratio ≥ 1.07, gain 1.11–3.48, lightness ≤ 1.34).

| Session | Path | Face / neck coh. (floor) | Cheek contrast ± SE (right / left) | Verdict |
|---|---|---|---|---|
| CG1 `125001-CG1-dfd4` | webcam → Meet | 0.484 / 0.343 (0.046) | +0.479 ± 0.040 | verify (provisional) |
| CG2 `125300-CG2-5677` | webcam → Meet | 0.455 / 0.275 (0.049) | +0.461 ± 0.010 | verify (provisional) |
| CG3 `125542-CG3-e2e0` | webcam → Meet | 0.502 / 0.375 (0.046) | +0.431 ± 0.044 (+0.209 / −0.222) | verify (provisional) |
| **CS1** `130047-CS1-swap-8123` | **FaceFusion swap** | 0.184 / 0.096 (0.046) | +0.332 ± 0.087 (+0.100 / −0.232) | **abstain** (weak) |
| CG4 `131818-CG4-1472` | webcam → Meet | – / 0.060 (0.046) | +0.221 ± 0.195 | abstain (no probe) |
| CG5 `132601-CG5-4f52` | webcam → Meet | – / 0.099 (0.045) | +0.216 ± 0.061 | abstain (weak) |
| CG6 `132933-CG6-65d4` | webcam → Meet | 0.196 / 0.167 (0.046) | +0.343 ± 0.033 (+0.148 / −0.195) | abstain (weak) |
| CF1 `133426-CF1-ff-noswap-cb82` | FaceFusion, swap off | 0.112 / 0.085 (0.035) | +0.309 ± 0.025 (+0.097 / −0.212) | abstain (weak) |
| **CS2** `133828-CS2-swap-07d5` | **FaceFusion swap** | 0.070 / 0.065 (0.034) | +0.187 ± 0.072 (+0.115 / −0.072) | **reject** (replay rule) |

Reading:

- **The live swap was not verified in either attempt** (CS1 abstain, CS2 reject).
- **Neither is a clean L3 result.** CS2 was rejected by the replay rule (neck 1.9x the floor, just
  under 2x, face–neck 0.58), not by L3. Signal through Meet plus the FaceFusion → OBS path was
  2–4x the floor (vs ~12x locally), and the subject's lighting and framing drifted after CG3
  (face brightness 44–48 → 66 in CG5; face size 35 → 28 px between CF1 and CS2). CS1 ran after
  the drift had started and its genuine comparison (CG3) used a different path.
- **Direction consistent with the hypothesis:** in both matched pairs the swap had less cheek
  lean than its genuine comparison (CG3 +0.431 → CS1 +0.332; CF1 +0.309 → CS2 +0.187), each time
  with one cheek's lean largely gone. Each difference is ~1.6 SE: suggestive, not conclusive.
- The FaceFusion → OBS path alone roughly halves the response (CG6 neck 0.167 vs CF1 0.085, same
  conditions minutes apart), so a swap test needs a genuine control through the same path.
- Next, to make L3 conclusive: run the swap test **locally on the attacker laptop** (probe full
  screen on that laptop, source = OBS Virtual Camera for CF/CS and the webcam for G), which removes
  Meet compression in both directions, and alternate CF/CS pairs several times without moving.

## 2026-10-06 — Call check 2 through Google Meet: the probe and the cheek lean survive the call

Same call as below, after selecting "OBS Virtual Camera" in Meet while a test pattern ran (both
people confirmed seeing it). Subject's room dimmed, ~30 cm, brightness 100%, facing the screen,
OBS "Real" scene (no swap). Split probe, amplitude 34, 60 s. Session
`20261006-124442-CALL-SMOKE-2-ac87`; tile 1763x882 read at 30 fps, face found in 100% of frames.

| Face / neck coh. (floor, K) | Ratio | Face/neck gain; bg/neck | Lightness | Cheek contrast ± SE (right / left) | Lag |
|---|---|---|---|---|---|
| 0.282 / 0.250 (0.070, 57) | 1.13 | 1.88; 0.45 | 1.42 | **+0.490 ± 0.065** (+0.245 / −0.245) | 1.37 s |

- The probe reaches the subject through Meet and returns measurably: neck 3.6x the floor (vs
  ~12x locally), with the face following as well as the neck.
- **The cheek lean survives the live call almost intact** (+0.49 vs +0.55–0.59 locally), about
  7 SE from zero, symmetric between the cheeks. One genuine subject, one session.
- Recorded verdict: **reject (replay)**, a false accusation: the neck was just under the 4x-floor
  gate, and the replay rule only checked that face and neck co-vary. **Rule changed:** replay now
  also requires the neck to show no response to today's probe (under 2x the floor); between 2x
  and the gate the verdict is abstain ("detected but too weak"). Re-judged: R1 (neck 1.2x floor)
  still rejects; this session abstains. Call sessions from here run 90 s (floor ≈ 0.047).

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

## 2026-10-06 — Live face swap, local, swap on/off through the same path (FaceFusion + OBS)

- Hardware: the attacker's laptop (Windows, its own webcam -> FaceFusion live swap in a Chrome
  window -> OBS Window Capture -> OBS Virtual Camera -> LUMEN `camera:1`). LUMEN ran on the same
  laptop with the probe full screen on its display (emit `screen`), so no video call and no codec
  beyond OBS.
- Conditions: one subject, same seat, facing the screen, split probe, amplitude 34, 90 s per
  session, room light not controlled beyond "steady". Exposure left on auto (virtual camera).
- Protocol: `experiments/swap_local/protocol.py`. Only FaceFusion's swap was toggled between
  sessions; CF = swap off, CS = swap on, alternated CF1 CF2 CF3 | CS1 CF4 CS2 CF5. L3 threshold
  calibrated from CF1–CF3 only (genuine sessions through the same path).
- Results as reported by the protocol on the attacker laptop (table transcribed from its output;
  the records are on that laptop and not yet copied here). Noise floor 0.045 throughout.

| Session | Swap | Video fps | Face / neck coh. | Cheek R / L | Cheek contrast ± SE | Verdict |
|---|---|---|---|---|---|---|
| CF1 | off | 24.9 | 0.311 / 0.293 | 0.222 / −0.286 | +0.508 ± 0.051 | verify |
| CF2 | off | 23.1 | 0.392 / 0.399 | 0.129 / −0.399 | +0.527 ± 0.034 | verify |
| CF3 | off | 23.8 | 0.302 / 0.241 | 0.185 / −0.236 | +0.422 ± 0.026 | verify |
| CS1 | **ON** | 24.7 | 0.272 / 0.261 | 0.080 / −0.259 | +0.339 ± 0.015 | **verify** |
| CF4 | off | 28.4 | 0.385 / 0.364 | 0.205 / −0.246 | +0.451 ± 0.045 | verify |
| CS2 | **ON** | 24.0 | 0.327 / 0.316 | 0.156 / −0.192 | +0.349 ± 0.047 | **verify** |
| CF5 | off | 27.1 | 0.692 / 0.694 | 0.196 / −0.305 | +0.501 ± 0.029 | verify |

**Result: the live swap was verified in 2 of 2 sessions. LUMEN did not detect it.** The L3 bound
calibrated from CF1–CF3 was +0.282 (+0.308 if all five swap-off sessions are used); both swap
sessions were above it.

What the swap did change: the cheek lean was lower in every swap session than in every swap-off
session (off +0.422 to +0.527, mean +0.482, n = 5; on +0.339 and +0.349, n = 2), a drop of about
29 %. Against the neighbouring swap-off sessions: CS1 −0.083 (2.8 SE) and −0.112 (2.4 SE); CS2
−0.102 (1.6 SE) and −0.152 (2.8 SE).

Reading: FaceFusion keeps most of the lateral shading of the camera frame (about 70 % of the
genuine lean), so the L3 hypothesis "a swap has no physical reason to reproduce the lean" holds
only partly for this generator: the lean is weakened, not removed. The gap between the two
groups (0.07) is smaller than the spread of genuine sessions allows a genuine-only threshold to
use. A threshold placed between them would be fitted to attack data, which PROJECT.md rules out,
so none is set. L1 and L2 also pass the swap: face and neck respond together in plausible
proportions, as expected when the generator inherits the frame's colour.

Not settled by this run: n = 2 swap sessions, one subject, one generator. What would change the
picture: more genuine sessions per subject (a tighter spread raises the bound), a stronger
lateral probe (higher amplitude, or the subject closer to the screen), and other generators.

## 2026-10-06 — Deep check, first candidates for catching the live swap (offline re-analysis)

Two physical measurements beyond L1–L3, tested on recordings already made (no new sessions).
Code: `src/lumen/deepcheck.py`, `experiments/deepcheck/neck_reference.py`.

**1. Face/neck response shape (does the face turn the probe's colours into the same pattern of
colour change as the neck?).** Meet sessions CG1–CG6, CF1 (FaceFusion, swap off), CS1 and CS2
(swap on), and local G1–G3. A plain similarity of the face and neck transfer matrices looked
promising (swap 0.80 and 0.82 against 0.93–0.97 for strong genuine sessions), but it is biased
low by noise: weak genuine sessions scored 0.71–0.86 too. With noise removed (transfer
estimated on two independent halves of the segments, cross-products only), the swap scored
1.00–1.10 and genuine 0.92–1.06. **No separation: FaceFusion reproduces the face's colour
response.** Dropped.

**2. Neck-referenced lean (cheek contrast / contrast between the neck's two halves).** Ten
genuine session videos re-measured with the neck split in two (J1 not measurable). The neck
leans too: neck contrast +0.146 to +0.304. But the ratio is not steadier than the cheek contrast
alone:

| | Mean | Spread between sessions (CV) | Within-session SE |
|---|---|---|---|
| Cheek contrast (L3) | +0.489 | 13.4 % | ~5 % |
| Cheek / neck contrast | +2.01 | 38.8 % (11 % without G1, neck +0.146) | ~10 % |

The neck's lean is smaller and noisier than the cheeks', so dividing by it adds noise. Not
usable as a reference. Not tested on swap sessions: none of them saved video.

## 2026-10-06 — Deep check: 3D relief + lean, the two-sign swap check (offline re-analysis)

Third candidate, `src/lumen/deepcheck.py` (`python -m lumen.deepcheck calibrate|check`). Separate
from the verdict path: the demo, the call launcher and decide.py are unchanged.

**Prediction, stated before the data was looked at:** a generator that recolours its output face
from the frame's overall colour spreads the probe evenly over the face, while on a real face the
nose and chin (standing out towards the screen) respond more strongly than forehead and cheeks.
**Relief** = coefficient of variation of the five face patches' probe gains, each gain estimated
noise-free from two independent halves of the segments.

Data: the attacker-laptop sessions of the local swap test (records copied from that laptop, all
signatures valid; same conditions as the entry above), and every genuine local session on the
dev laptop. No new recordings.

| | Relief | Lean (cheek contrast) |
|---|---|---|
| Swap off, CF1–CF5 (attacker laptop) | 0.249–0.484 | +0.422 to +0.527 |
| **Swap on, CS1, CS2** | **0.145, 0.160** | **+0.339, +0.349** |
| Genuine, dev laptop, 17 sessions (rooms, days, people) | 0.237–0.513 | +0.193 to +0.589 |
| Through Google Meet: genuine CG1–CG6 / swap CS1, CS2 | 0.21–0.73 / 0.41, 0.30 | — |

On the real face the nose and chin responded about twice as strongly as the forehead (attacker
laptop: nose 1.4–2.7, chin 1.7–2.7, forehead 0.7–1.1, relative to the neck); under the swap all
patches came out between 0.9 and 1.7.

**Judgement rule:** swap only when BOTH relief and lean are below genuine-only bounds
(mean − 2·sqrt(sd² + SE²)). Rule fixed before the held-out sessions below were judged.

1. Protocol order on the attacker laptop: calibrated on CF1–CF3 (lean ≥ +0.3498, relief ≥
   0.2361), then judged the later sessions: **CS1 swap, CS2 swap, CF4 real, CF5 real (4/4)**.
   Margins are thin: CS2's lean +0.349 against +0.3498; CF4's relief 0.249 against 0.236.
2. False alarms on the dev laptop: calibrated on E1–E3 (lean ≥ +0.3979, relief ≥ 0.3365), judged
   11 other genuine sessions (G1–G3, J2–J4, demo sessions; other days, rooms and people): **11 real,
   0 swap**; one 30 s demo session abstained (probe too weak). **Relief alone would have flagged
   4 of the 11** (G1 0.322, e1c7 0.287, f2cc 0.237, cf18 0.246): relief varies between sittings,
   so it is only usable as the second of two signs.
3. Through Google Meet the relief does not separate (swap 0.41 and 0.30 inside the genuine range):
   **the deep check is not validated for calls**, only with the probe on the subject's own screen.

What this is and is not: two live-swap sessions from one generator (FaceFusion), one subject on
one laptop, and the thresholds from three sessions each. A promising physical sign that matched a
stated prediction, not a validated detector. Next: more swap and swap-off sessions (with
`--save-video`), a second subject, a second generator.

### Deep check: leave-one-out and calibration sensitivity (2026-10-06, same records)

`experiments/deepcheck/evaluate.py` reproduces the held-out table above exactly, and adds:

- **Attacker laptop, leave-one-out over CF1–CF5:** 0 false alarms in 5. **But calibrated on all
  five swap-off sessions, CS2 is missed** (relief 0.160 against a bound of 0.154, because CF4's
  0.249 widens the spread; judged "real, lean low"); CS1 is still caught. Detection is 2/2 or
  1/2 depending on which genuine sessions calibrate it: the margin is too thin to call robust.
- **Dev laptop, leave-one-out over 14 genuine sessions** (G1–G3, E1–E3, J2–J4, five demo
  sessions; other days, rooms and people): **0 false alarms in 14.**
- The two swap sessions judged by the dev-laptop pool's calibration (lean ≥ +0.3551, relief ≥
  0.2002): both swap. Confounded: different laptop, camera and screen from the swap sessions, so
  this is not evidence that one calibration transfers between machines.

## 2026-10-07 — Verification link: first genuine session through the browser (W1)

- Hardware: dev laptop, Chrome, `http://localhost:8443` (`python -m lumen.web --http`), Integrated
  Camera through `getUserMedia`, 640x480, JPEG quality 0.92 uploads; display at 144 Hz (12 959 probe
  frames drawn in 90 s).
- Conditions: one subject, the auditorium at night (decorative lights behind), ~40 cm, facing the
  screen, split probe, amplitude 34, 90 s. Auto exposure first gave 10 fps; the page's exposure
  tuning (longest exposure that still gives >= 20 fps) brought it to 19–21 fps. Exposure and white
  balance otherwise automatic (browser).
- Session: 20261006-200335-web-aba1 (UTC id). Signature valid; every drawn colour regenerated from
  the nonce on the server.

| Neck coh. | Face coh. | Floor (K) | Gain | Cheek contrast | Relief | Lag | fps | Verdict |
|---|---|---|---|---|---|---|---|---|
| 0.691 | 0.794 | 0.045 (88) | 1.552 | +0.560 | 0.201 | 0.100 s | 19.1 | verify (provisional: web path not calibrated) |

The probe survives the browser path: neck 15x the floor, and the cheek contrast (+0.560) is in the
range of the local sessions on this laptop (+0.40 to +0.59). One earlier attempt
(20261006-195914-web-9070) ran with the first, fixed exposure that left the picture nearly black: face found in 0.9 % of frames, abstained ("not enough data to measure").

### Verification link: web-path calibration (2026-10-07, same subject, laptop and room as W1)

Three genuine sessions opened with `?label=genuine`, after the page's exposure tuning was fixed
(every setting measured after settling; brightest setting with >= 20 fps kept).

| Session | Neck coh. (floor) | Cheek contrast | Relief | fps |
|---|---|---|---|---|
| 20261006-202050-web-genuine-a551 | 0.696 (0.045) | +0.607 | 0.150 | 19.4 |
| 20261006-202357-web-genuine-5949 | 0.696 (0.045) | +0.529 | 0.191 | 19.1 |
| 20261006-202600-web-genuine-0922 | 0.699 (0.045) | +0.543 | 0.201 | 19.9 |

`python -m lumen.web --calibrate`: L1–L3 cheek contrast >= +0.4244, face/neck coherence ratio
>= 0.9231; deep check lean >= +0.4694, relief >= 0.1243.

- **Held-out genuine W1 abstains** under this calibration: lamp gate, face/neck lightness 1.294
  against a limit of 1.285 from three very similar sessions. Not retuned. The deep check calls W1
  real.
- **Relief is low through the browser:** 0.150–0.201 for this genuine face, against 0.237–0.513
  for the same face with the local camera, and 0.145 / 0.160 for the FaceFusion swap locally (other
  laptop). Likely cause, not yet tested: JPEG 4:2:0 halves the colour resolution and mixes
  neighbouring face patches. Whether relief can still separate a swap through the link is open
  until a swap session runs through it.

## 2026-10-07 — Live face swap through the verification link (attacker laptop -> Wi-Fi -> dev laptop)

- Setup: the attacker laptop opened `https://<dev laptop>:8443` (phone hotspot, self-signed
  certificate) in Chrome with **OBS Virtual Camera** as the camera: webcam -> FaceFusion (Chrome
  window) -> OBS Window Capture -> OBS Virtual Camera -> the verification page. The probe filled
  the attacker laptop's screen; the dev laptop's server did all the measuring. One subject, same
  seat, 90 s per session, split probe, amplitude 34. Only FaceFusion's swap was toggled. Sessions
  tagged `ff-genuine` = swap off, `ff-swap` = swap on.
- Calibration: the attacker path's own genuine sessions, ff-genuine 1–3 (`--calibrate --tag
  ff-genuine`): L3 cheek contrast >= +0.3437; deep check lean >= +0.4119, relief >= 0.098.
- Two sessions recorded before the server sorted frames by capture time (5 and 19 of 2700 frames
  arrived out of order) are analysed in time order; their signed files are unchanged.

| Session | Swap | Neck coh. (floor) | Cheek contrast | Relief | L1–L3 | Deep check |
|---|---|---|---|---|---|---|
| ff-genuine-a94e | off | 0.652 (0.045) | +0.477 | 0.211 | verify | real |
| ff-genuine-dcbe | off | 0.658 (0.045) | +0.605 | 0.135 | verify | real |
| ff-genuine-5948 | off | 0.660 (0.045) | +0.563 | 0.194 | verify | real |
| ff-swap-d33c | **on** | 0.528 (0.045) | +0.412 | 0.112 | **verify** | **real** |
| ff-genuine-b604 | off | 0.706 (0.045) | +0.541 | 0.188 | verify | real |
| ff-swap-d65f | **on** | 0.514 (0.045) | +0.391 | 0.138 | **verify** | **real** |
| ff-genuine-41f3 | off | 0.714 (0.045) | +0.560 | 0.293 | verify | real |

**Result: the live swap was verified in 2 of 2 sessions through the link. Neither L1–L3 nor the
deep check detected it** (the first swap's lean was 0.0001 above the deep bound). As in the local
test, the swap lowered the cheek contrast (−27 %, both below all five swap-off sessions) and the
neck response (−23 %), but not past genuine-only bounds.

### Exploratory: the swap pipeline is slow and smears fast flicker (found after the fact)

Looking for why the swap lowered even the neck's response (the swap does not touch the neck),
two quantities separated swap from swap-off completely. **They were found after looking at the
link sessions above**, then checked on the earlier local swap sessions (records from the attacker
laptop, no browser, recorded hours before), which had not been used to find them.

| Data | Neck coherence 5–8 Hz ÷ 2–4.5 Hz | Pipeline delay (lag) |
|---|---|---|
| Link, swap off (5) | 0.75–0.80 | 0.167 s (all) |
| Link, **swap on** (2) | **0.57, 0.53** | **0.300 s** (both) |
| Local, swap off CF1–CF5 (independent check) | 0.61–0.74 | 0.133 s (all) |
| Local, **swap on** CS1, CS2 (independent check) | **0.29, 0.34** | **0.333 s** (both) |
| Dev laptop, genuine, direct webcam (local G/E/J/demo sessions and link W1 + 3; 17) | 0.83–0.97 | 0.100–0.133 s |
| Lamp L1 (genuine, torch on the face) | 0.77 | 0.133 s |
| Through Google Meet, genuine CG1–CG6 / swap CS1, CS2 / FaceFusion off CF1 | 0.39–0.81 / 0.48, 0.43 / 0.50 | 1.0–1.5 s |

Reading: a real face reflects the probe instantly, so it follows the fast part of the band nearly
as well as the slow part, and the delay is the camera's. A face-swap pipeline renders every
frame, adding ~0.15–0.2 s and smearing fast changes (OBS re-times its output to an even 30 fps,
so the frame timestamps look regular). 4 of 4 swap sessions show both signs, 0 of 10 swap-off
sessions on the same laptop do.

Limits: found post hoc (the local data is the only independent check); the direct-webcam range
comes from one laptop's camera only; through Google Meet it does not separate at all (the call's
own processing smears every session); a faster GPU or a direct virtual-camera output may shrink
both effects. A lead for a fourth layer ("temporal fidelity"), not a validated detector; next:
other webcams for the genuine range, a second generator, thresholds fixed before testing.
