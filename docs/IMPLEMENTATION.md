# What we built, and how

LUMEN checks whether the person in a live video is **physically present**, by measuring how their
face reflects a secret light pattern we send, never by judging how the face looks. Everything
below runs offline on a laptop CPU. Measured numbers are from `docs/RESULTS.md` (one subject,
2026-10-05/06); anything simulated says so.

```
one-time code ──► probe generator ──► screen / virtual camera ──► light falls on the subject
                                                                         │
signed record ◄── verdict ◄── physics checks ◄── face/neck/cheek colours ◄── camera / call video
```

---

## 1. One-time light probe (`probe.py`)

**What:** a faint, flickering colour pattern that nobody can predict, different on the left and
right halves of the screen.

**How:**
- Each session draws a fresh **128-bit secret code** (Python `secrets`).
- The pattern is a sum of soft cosine waves at every frequency between **2 and 8 Hz** that fits a
  whole number of cycles in the session (361 waves per colour axis for a 60 s session). Their
  starting phases come from **SHA-256 of the code** in counter mode, so the same code always
  regenerates the same pattern, on any machine, and a different code gives an unrelated one.
- Colours are defined in **CIELAB**: lightness is held constant (L* = 70) and only the two colour
  axes (green–red, blue–yellow) move. The screen never gets brighter or darker
  (**isoluminant**), which is gentler to look at and does not trigger the camera's auto-exposure.
- Amplitude is normalised exactly using an inverse FFT over a dense time grid; before showing
  anything, every colour is checked to be displayable (inside the sRGB gamut) or the session
  refuses to start, so no hidden clipping can change brightness.
- **Two independent sequences** (seeded from `code|left` and `code|right`) drive the two halves.
- The pattern never loops, so it cannot be learnt and replayed.

**Tools:** NumPy, `hashlib`, `secrets`.

## 2. Showing the light (`emitter.py`)

- Draws the pattern ~60 times a second and **logs the exact time and colour of every frame**, on
  the same high-resolution clock the camera uses (alignment is by time, never by frame number).
- **8x8 Bayer ordered dithering** lets an 8-bit screen show colours between whole steps: the
  average over the screen, which is what lights the face, keeps sub-step precision.
- The window forces itself **always on top** through the Windows API (`ctypes`), and records
  whether that worked.
- **Call mode:** instead of a window, the pattern is sent into a **virtual camera** (pyvirtualcam
  + OBS driver), so Zoom/Meet/Teams carry it to the other person's screen. Measured loopback: both
  halves arrive on the correct side within 1 colour unit.

**Tools:** OpenCV (display), Win32 API, pyvirtualcam, OBS Studio virtual camera.

## 3. Capturing the video (`capture.py`)

- A dedicated **grabber thread** reads and timestamps each frame the moment it arrives, so slow
  processing drops frames (counted) instead of corrupting timing.
- Sources: **webcam**, **recorded video** (replayed at its original frame times), **a screen
  region** (the other person's tile in a call, picked by dragging a box), or an **MJPEG stream**.
- Detects an **unplugged camera** (repeated failed reads) and reports it.
- Tries to **lock exposure and white balance** by matching manual settings to the auto image,
  and always restores auto mode safely (a driver bug that left the webcam at 10 fps was found on
  real hardware and fixed).

**Tools:** OpenCV (DirectShow), `mss` (screen capture), threads.

## 4. Finding face, neck and cheeks (`regions.py`)

- **YuNet**, a small ONNX face detector from OpenCV's model zoo, gives the face box and 5
  landmarks (eyes, nose, mouth corners). It only *places regions*; it never judges the face.
- From the landmarks we build a **face coordinate frame** (scaled by eye distance and
  eye-to-mouth distance, rotated with head tilt) and draw: the face skin (eyes and mouth cut
  out), the **neck** below the chin, the **background** beside the head, and small patches on the
  **left cheek, right cheek**, forehead, nose and chin.
- Landmarks are smoothed between frames; work is done on a downscaled frame: **3.6–5 ms per
  frame measured** on the laptop CPU.
- Also estimates **head turn** (yaw) and **face size** (distance), used by the safeguards.

**Tools:** OpenCV `FaceDetectorYN`, YuNet model (committed, so no internet is needed).

## 5. The physics measurements (`features.py`)

For each region, the average colour per frame becomes a time series, compared with the light we
sent.

- **Delay:** the light takes time to come back (screen → face → camera → call). We estimate it
  from the **neck**, after band-pass filtering to 2–8 Hz so slow head movement cannot hide it,
  and apply the same delay to every region. The face is never tuned separately (that would let
  noise look like signal).
- **Coherence (L1):** *Welch multiple coherence*: the fraction of a region's colour change, at
  each frequency, that our light explains. Computed as a multi-input model (`H = Grp · Gpp⁻¹`),
  so it works whatever the camera's colour axes are. Verified against SciPy to 1e-10.
- **Noise floor:** with no response, coherence is about p/K (p = probe channels, K = segments);
  reported with every result so "high" always means "high above chance".
- **Gain (L2):** how strongly each region responds; face vs neck, background vs neck.
- **Lateral lean (L3):** from the same model, how much of each region's response comes from the
  left half vs the right half of the screen. **Cheek contrast** = right cheek's lean minus left
  cheek's lean: positive for a real 3D face.
- **Error bars:** spectra are kept per segment, so a **block jackknife** gives the cheek contrast
  ± its standard error without re-running anything. Checked: the reported error matches the real
  spread over 40 simulated sessions.

**Tools:** NumPy, SciPy (`signal`: windows, detrending, Butterworth band-pass).

## 6. The decision: verify / reject / abstain (`decide.py`)

Checks run in order; the first that decides, decides, and **every check is reported with its
value and threshold**.

| Stage | Check | If it fails |
|---|---|---|
| Data quality | camera ≥ 16 fps (twice the top probe frequency; below that the flicker cannot be seen at all), enough probe time, no long gaps, face found | abstain |
| Pose & distance | head not turned, face not much farther than in calibration | abstain |
| Signal | the neck follows today's light above the noise floor | abstain, or **reject as replay** if face and neck follow each other but not today's code |
| Lighting | face not lit much more than the neck (a lamp) | abstain |
| L1 | face follows the light as well as the neck | reject |
| L2 | face/neck/background in physically possible proportions | reject |
| L3 | cheeks lean towards the halves they face | reject |

- **Calibration uses genuine sessions only** (never fake examples, so nothing for a generator to
  train against). The L3 bound is mean − 3 x sqrt(between-session spread² + within-session
  error²), so a real person is not rejected because three calibration sessions happened to agree
  closely. It also learns whether the call app **mirrors** the video.
- If the face is lost for a moment, the **longest gap-free stretch** is analysed instead of
  inventing data across the gap.

## 7. Tamper-proof session records (`record.py`)

- Every session writes `record.json`: the code (revealed after the session), every frame of light
  sent, every colour measured, all checks and the verdict.
- Signed with **Ed25519** over canonical JSON. Verification checks the signature **and
  regenerates the light pattern from the code** and compares it frame by frame (to 1e-6). Change
  one character and it fails; a replayed video follows an old code and fails.

**Tools:** `cryptography` (Ed25519).

## 8. Running a session (`session.py`)

Three threads (grabber, processor, emitter) on one clock: 8 s grey warm-up (the camera settles,
exposure lock runs), then the probe, then analysis, verdict and signed record. A live snapshot
feeds the console every 300 ms.

## 9. Operator console (`api.py`, `console/`)

- **FastAPI** backend; plain HTML/CSS/JavaScript front end with **no external files**, so it
  works with the network unplugged.
- Big verdict with a step tracker, three cards for L1/L2/L3 in plain language (with a cheek-lean
  diagram), live traces of the light vs face vs neck, safeguard chips that explain every
  abstain, session history, one-click calibration, signed-record download and drag-and-drop
  record verification, dark/light themes.

## 10. Tools for running it

- `python -m lumen run | smoke | serve | calibrate | verify | reanalyse | cameras | pick-region`
- `lumen-demo.cmd`: a double-click menu that asks "ready?" before every session.
- `docs/RUNBOOK.md`: step-by-step instructions and troubleshooting.

## 11. Experiments

- **M1 report** (`experiments/m1_probe_survival/report.py`): results table where each genuine
  session is judged by thresholds calibrated *without* it (leave-one-out).
- **Codec survival** (`experiments/codec_survival/run.py`): re-encodes video through **H.264 and
  VP8** at call bitrates (PyAV) and re-measures. *Simulated* so far: the light survives, the
  cheek lean roughly halves, so calibration must go through the same call path.
- **Attacker setup** (`docs/SWAP_SETUP.md`): FaceFusion live face swap on a second laptop over a
  Google Meet call.

## 12. Testing

**183 automated tests**, all on signals with known answers: coherence vs SciPy, noise floor,
delay recovery under drift, isoluminance, dithering precision, signatures and tampering,
thread races, camera failures, the full API, and seven simulated scenarios (genuine, swap that
copies the light, unlit swap, replay, lamp, too far, uniform relighting).

---

## Measured on real hardware (laptop webcam, one subject)

| Test | Result |
|---|---|
| 3 genuine sessions (60 s, auditorium) | **verify** all three; face/neck coherence 0.86–0.90 vs noise floor 0.07 |
| Cheek lean on a real face | right cheek +0.37, left −0.20: opposite, as predicted; contrast +0.54…+0.59 (± 0.03) |
| Background (far wall) | responds at 0.06–0.08 of the neck: light falls off with distance |
| Replay of a recorded genuine session | **reject**: neck follows today's code at 0.094 (floor 0.078) |
| Torch on a real face | **abstain**: without that safeguard L1 and L3 would have wrongly rejected a real person |
| Sitting farther back | cheek lean halved (+0.50 → +0.27): led to the distance safeguard |
| Probe through the OBS virtual camera | colours arrive within 1 unit, halves not mirrored |

**Not measured yet:** a live face swap, and anything through a real call. Next step.

## Problems we hit and solved

- **Windows Application Control** blocks some native libraries on the dev laptop: MediaPipe → we
  use OpenCV YuNet; SciPy 1.18 and PyAV 17 → pinned to versions that load; Deep-Live-Cam's
  `insightface` build → the attack runs on a second laptop with FaceFusion.
- **Webcam stuck at 10 fps** after our exposure search → search limited to one frame period,
  auto mode restored from a short exposure, and a Nyquist safeguard added.
- **Replays played 1.4x too fast** (container said 30 fps, camera gave 20.8) → replay at recorded
  frame times.
- **A lamp made a real person look like a swap** → lamp safeguard abstains (measured).
- **Calibration did not transfer between rooms** → documented, and the console warns when
  thresholds are provisional.
- **Distance changes the 3D lean** → distance safeguard.
- **A race between threads** in the live view → consistent snapshots, with a regression test.

## What is new here (and what is not)

Screen-light liveness exists (Face Flashing, NDSS 2018; iProov's commercial check needs their
app). LUMEN's combination, to our knowledge, is: **zero cooperation and no software on the other
person's device** (it rides an ordinary call), **differential** measurement (face vs neck,
left cheek vs right cheek) so the camera, codec and room cancel out, a **two-sided** probe that
tests 3D shape, an honest **abstain**, and **independently verifiable** signed records. It is not
unbeatable: an attacker who could re-light a fake face in real time under light they cannot
predict would defeat it; no such tool is known.
