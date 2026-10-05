# Milestone 1 protocol: does the physics hold?

Goal: measure, on real hardware, whether the probe survives capture, whether a live face swap
carries it, and whether the two-sided probe's cheek contrast separates a real face from a swap.
Report the numbers either way (PROJECT.md §5).

## Before you start

1. `python -m lumen cameras` — note the index of the webcam and of the virtual camera.
2. `python -m lumen smoke --source camera:<n>` — check: frame rate near 30 fps, face and neck
   found in > 95% of frames, processing well under 33 ms/frame. Note whether exposure and white
   balance locked.
3. Write down: room lights on, windows/daylight, display brightness (set it to 100%), distance from
   eyes to screen (aim for ~40 cm), the probe amplitude.
4. Consent: every person whose face is used as a swap *source* or *target* agrees in writing.

## Sessions (same room, same light, same pose; 60 s each)

Record genuine sessions *around* each attack session (genuine – attack – genuine), so drift in
pose or light shows up as a difference between the two genuine runs rather than hiding in the
attack result.

| Label | Source | What happens |
|---|---|---|
| `G1`, `G2`, … | `camera:<webcam>` | the subject, facing the screen, still |
| `S1`, … | `camera:<virtual>` | the same subject through a live face-swap tool into a virtual camera |
| `R1` | `file:data/sessions/<G1 id>/video.mkv` | replay of a genuine session (record G1 with `--save-video`) |
| `L1` | `camera:<webcam>` | a desk lamp aimed at the face (the abstain drill) |
| `P1`, … | `camera:<webcam>` | head turned ~15° left, then right (pose range) |

Commands:

```powershell
python -m lumen run --source camera:0 --duration 60 --label G1 --save-video
python -m lumen run --source camera:1 --duration 60 --label S1
python -m lumen run --source "file:data/sessions/<G1 id>/video.mkv" --duration 60 --label R1
```

Run at least three sandwiches (G–S–G), and repeat one with `--single` to compare layouts.

## Analysis

```powershell
python experiments/m1_probe_survival/report.py <session ids...>
```

prints a table (face and neck coherence, floor, gain, cheek contrast, lag, verdict) and writes it
to `experiments/m1_probe_survival/results/<timestamp>/`. Calibrate from the genuine sessions only:

```powershell
python -m lumen calibrate <G ids...>
```

then re-run the report to see each session's verdict under those thresholds, using leave-one-out
for the genuine sessions so no session is judged by thresholds it helped set.

## Codec loss (needs sessions recorded with `--save-video`)

```powershell
python experiments/codec_survival/run.py --session <G id> --session <S id>
```

re-encodes each lossless recording through H.264 and VP8 at call-like bitrates and re-measures.
This is a measurement on real footage (record it in `docs/RESULTS.md`). If cheek contrast shrinks
under compression, as it does in simulation, the deployment must be calibrated through the same
call path it will judge.

## Through a real call (after the local sessions)

The live face-swap session is run this way, with the attacker on a second laptop; full setup in
`docs/SWAP_SETUP.md`.

Repeat one G–S–G sandwich with the probe carried by a call: verifier laptop with
`--emit virtualcam` (OBS Virtual Camera selected in Meet/Zoom), subject on a second device with
the verifier's video full screen, source from `python -m lumen pick-region`. Write down the call
app, both devices, the tile size in pixels, and whether calibration found the video mirrored
(`cheek_contrast_sign` in `data/thresholds.json`).

## Decision rules

- **Probe survives?** Genuine neck coherence well above the floor (≥ 4 × p/K) at the chosen
  amplitude. If not: raise the amplitude (stay in gamut), move closer, or lengthen the session.
- **L1 against the swap:** if swap face coherence is close to genuine, L1 is weak against that
  tool; L3 must carry it.
- **L3:** genuine and swap cheek contrast ranges must not overlap across sandwiches before any
  claim is made. One subject and one tool is a pilot, not a result.

Write everything — including failures and sessions that do not count — into `docs/RESULTS.md`.
