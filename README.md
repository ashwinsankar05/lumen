# LUMEN

Proof of physical presence for live video, by measurement rather than appearance.

LUMEN lights the person on the other end of a video call with a faint, one-time colour pattern
from the verifier's own screen (independent sequences on the left and right halves), then measures
how the returning video responds: face against neck, and each cheek against the half of the
screen it faces. It returns **verify**, **reject** or **abstain**, with every check that led there,
in an Ed25519-signed record that anyone can re-check from the one-time code.

It never asks "does this face look fake?" (see `PROJECT.md` §2).

> **Status (2026-10-05):** the detector, console and records are built and tested on
> **simulated** signals only. Nothing has been measured on a camera yet; `docs/RESULTS.md` is
> where measurements will go, and `docs/PROTOCOL.md` says how to take them.

## Setup (Windows, laptop CPU)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m pytest            # simulated-signal tests; no camera needed
```

The YuNet face detector (`models/`) is committed, so nothing is downloaded at run time and nothing
in the live path needs a network or a GPU. SciPy is pinned below 1.16: a native extension in later
versions is blocked by Windows Application Control on the development laptop.

## Use

```powershell
.venv\Scripts\python -m lumen cameras                  # which camera indices open (real + virtual)
.venv\Scripts\python -m lumen pick-region              # select a video-call tile as the source
.venv\Scripts\python -m lumen smoke                    # 20 s: frame rate, exposure lock, face found
.venv\Scripts\python -m lumen run --label genuine-1    # one session; verdict in the terminal
.venv\Scripts\python -m lumen serve                    # operator console: http://127.0.0.1:8000
.venv\Scripts\python -m lumen calibrate <id> <id> <id> # thresholds from genuine sessions
.venv\Scripts\python -m lumen verify data\sessions\<id>\record.json
```

Sources: `camera:<n>` (a webcam or a virtual camera), `file:<video>` (a recording played as a
camera, for the replay drill), `screen:<x>,<y>,<w>,<h>` (the subject's tile in a video call),
`mjpeg:<url>`, and `synthetic:<scenario>` — **simulated** scenes for checking the plumbing:
`genuine`, `swap_inherit`, `swap_unlit`, `replay`, `lamp`, `far`, `relit`. Records from synthetic
sources are marked `simulated` everywhere.

`--single` uses one probe sequence for the whole screen; that disables L3 (see `PROJECT.md` §4).

### Call mode (verifying someone on Zoom / Meet / Teams)

In a call, the light on the subject comes from *their* screen, which shows *our* video. So the
probe goes out as our camera feed and we measure their tile on our screen:

1. Install [OBS Studio](https://obsproject.com/) (28 or later) and start it once; it registers the
   "OBS Virtual Camera" driver. Close OBS afterwards.
2. In the call, choose **OBS Virtual Camera** as your camera and ask the subject to make your
   video large (full screen or speaker view) and keep their face in view.
3. `python -m lumen pick-region` — drag a box round the subject's tile; it prints a `screen:` source.
4. `python -m lumen run --source "screen:x,y,w,h" --emit virtualcam --label call-genuine-1`

Calibrate from genuine sessions recorded **through the same call app** (`PROJECT.md` §4): the
codec weakens the cheek lean, and some apps mirror the video, which calibration detects.

## How it decides

| Step | Check | Outcome on failure |
|---|---|---|
| Data quality | enough probe time, no long gaps, face + neck found | abstain |
| Head pose | not turned beyond the calibrated range | abstain |
| SNR gate | neck follows today's probe above the noise floor p/K | abstain, or reject if face and neck follow each other but not the probe (replay) |
| Lamp gate | face not lit more strongly relative to the neck than in calibration | abstain |
| L1 | face coherence ≥ fraction of neck coherence | reject |
| L2 | face/neck gain in the genuine range; background falls off with distance | reject |
| L3 | cheek contrast (two-sided probe) ≥ genuine mean − 3 sd | reject |

Thresholds come only from genuine sessions recorded in the same room and light; until you
calibrate, every verdict is flagged *provisional*.

## Layout

```
src/lumen/     colour, probe, emitter, capture, regions, features, decide, record, session, api, synthetic
console/       single-page operator UI (no CDN)
models/        YuNet face detector (MIT, OpenCV model zoo)
experiments/   m1_probe_survival/report.py, codec_survival/run.py (H.264 / VP8 round trips)
docs/          RESULTS.md (measured only), PROTOCOL.md, DEMO.md
tests/         simulated-signal, pipeline, session and API tests
data/          sessions, keys, thresholds (git-ignored)
```

Team Owlight — Hackathena, Jyothi Engineering College.
