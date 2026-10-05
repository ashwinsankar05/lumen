# Demo runbook

Four scenarios (PROJECT.md §11). Everything runs on the laptop CPU with the network unplugged;
the GPU is used only by the face-swap tool in scenario 2.

## Before the judges arrive (same room, same light as the demo)

1. Unplug the network. Start the console: `python -m lumen serve`, open http://127.0.0.1:8000.
2. Display brightness 100%. Subject ~40 cm from the screen, facing it.
3. Record three genuine sessions of the presenter (label `cal-1..3`), tick them in the session
   list and press **Calibrate**. The header must then read *calibrated thresholds*.
4. Record one genuine session with **save video** on; this file is the replay in scenario 3.
5. Start the face-swap tool and its virtual camera; check `python -m lumen cameras` lists it.

## Scenarios

| # | Source | Expected | Talking point |
|---|---|---|---|
| 1 Baseline | `camera:0`, the judge | verify | face and neck move with the probe; the cheeks lean towards the halves they face |
| 2 Live swap | the virtual camera | reject (L3) | the swapped face can carry the colour, but not the 3D lean |
| 3 Replay | `file:<saved video>` | reject (replay) | face and neck follow an old one-time code, not today's |
| 4 Lamp | `camera:0` with a desk lamp on the face | abstain | not enough probe on the face to decide; it refuses to guess |

After each: download the signed record and drop it into **Verify a record**.

## Failure drills

- **No network:** already unplugged; nothing changes.
- **Camera unplugged mid-session:** the capture error is reported and the verdict is abstain.
- **Bad light / subject far away:** the neck SNR gate fails; abstain.

## What not to say

- Do not quote any number that is not in `docs/RESULTS.md`.
- Do not say it is unbeatable: real-time inverse rendering of skin under an unpredictable
  illuminant would defeat it; no implementation is known (PROJECT.md §9).
- Do not say we invented active liveness (PROJECT.md §10).
