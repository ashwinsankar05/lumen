# Runbook: running LUMEN yourself

Everything below is typed in **PowerShell** (Start menu → "PowerShell"), one line at a time.
Every command starts from the project folder, so begin each time with:

```powershell
cd C:\Users\LOQ\lumen-v2
```

We call Python as `.venv\Scripts\python` instead of "activating" the environment: activation
scripts are often blocked by Windows, this way never is.

Before any session that turns on the camera or fills the screen, tell the person in front of it
and wait for their "ready" (PROJECT.md §12).

---

## 1. One-time setup on a laptop

```powershell
cd C:\Users\LOQ\lumen-v2
git pull origin main                                  # latest code from GitHub
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .             # makes "python -m lumen" work
.venv\Scripts\python -m pytest -q                     # ~2 min; expect "... passed"
```

If `.venv` does not exist yet (a fresh laptop): `python -m venv .venv` first.

**Recordings and calibration** live in `data\` (never uploaded to GitHub). On the dev laptop
the sessions recorded on 2026-10-05 are in the work folder; copy them once:

```powershell
Copy-Item -Recurse C:\Users\LOQ\lumen-v2\.claude\worktrees\rebuild\data C:\Users\LOQ\lumen-v2\data
```

**OBS Studio** must be installed for call mode only (`winget install OBSProject.OBSStudio`).
Keep the OBS app **closed** while LUMEN runs; LUMEN drives OBS's virtual camera itself.

## Shortcut: the demo menu

After the one-time setup, **double-click `lumen-demo.cmd`** in `C:\Users\LOQ\lumen-v2` (or run it
in a terminal). It offers: camera check, genuine session, replay attack (replays the newest saved
video), lamp test, calibration session, calibrate, verify the newest record, list sessions, and
the console. Every session asks "ready? (y/n)" first. `lumen-demo.cmd 2` runs option 2 once.
The sections below are the same steps as typed commands.

## 2. Check the camera (30 s)

```powershell
.venv\Scripts\python -m lumen cameras          # which cameras open, e.g. "camera:0 640x480"
.venv\Scripts\python -m lumen smoke            # 8 s grey + 30 s probe, full screen
```

Good result: frame rate **≥ 16 fps** (ideally 20+), "face+neck found" near 100%, processing a
few ms per frame. Below 16 fps LUMEN abstains: add light, set brightness to 100%.

Esc in the probe window stops a session at any time.

## 3. Record one session

```powershell
.venv\Scripts\python -m lumen run --duration 60 --label G1 --save-video
```

| Option | Meaning |
|---|---|
| `--source camera:0` | which camera (default). `file:<video>` replays a recording; `screen:x,y,w,h` reads part of the screen |
| `--duration 60` | seconds of probe (after an 8 s grey warm-up). 60 s gives the cleanest numbers |
| `--label G1` | your name for the session (shows in the session id) |
| `--save-video` | also saves the lossless video (≈280 MB per minute), needed for replay tests |
| `--windowed` | probe in a window instead of full screen (weaker light; only for quick checks) |
| `--single` | one sequence for the whole screen; disables the cheek check (L3) |
| `--emit virtualcam` | call mode: probe goes out through the OBS virtual camera (section 7) |

The subject: ~40 cm from the screen, facing it, still, face and neck visible, room lights on,
screen brightness 100%.

Each session prints the **verdict**, every check with its value and threshold, and the path of
its signed record: `data\sessions\<session id>\record.json`. List sessions with
`dir data\sessions`.

## 4. Calibrate (after a room change, or before a demo)

Thresholds come from **genuine** sessions in the same room and light. Record three genuine
sessions (section 3, labels like `G1 G2 G3`), then:

```powershell
.venv\Scripts\python -m lumen calibrate <id of G1> <id of G2> <id of G3>
```

Use the full folder names from `dir data\sessions`. Thresholds are saved to
`data\thresholds.json`; until it exists, every verdict says "provisional" and the cheek check
(L3) is not enforced. Calibrating in one room and judging in another is not valid.

## 5. Reading a verdict

| Verdict | Means |
|---|---|
| **VERIFY** | face and neck follow today's probe together, in plausible proportions, and the cheeks lean towards the screen halves they face |
| **REJECT** | a physical check failed: L1 (face does not follow the probe like the neck), L2 (wrong proportions / no distance falloff), L3 (no 3D cheek lean), or replay (follows an old code) |
| **ABSTAIN** | not enough evidence to decide: too short, face lost, camera too slow, head turned, too little probe light, or a lamp on the face. It refuses to guess |

The reason line names the check that decided. The table under it lists every check
(`pass` / `FAIL` / `--` not evaluated).

## 6. The operator console (for showing people)

```powershell
.venv\Scripts\python -m lumen serve
```

Open http://127.0.0.1:8000 in a browser. Pick a source, press **Start**. The probe window covers
the screen during the session; the verdict, checks and live traces are on the page afterwards
(with a second monitor you can watch them live). The page also lists all sessions, calibrates
from ticked genuine sessions, downloads signed records and checks a record you drop in.

`synthetic:genuine`, `synthetic:swap_inherit`, `synthetic:replay`, `synthetic:lamp` … run
**simulated** scenes with no camera: useful to show the logic, always say they are simulated.

Ctrl+C in PowerShell stops the server.

## 7. Call mode (verifying someone on Google Meet)

**Shortcut: `lumen-call.cmd`** (double-click; separate from the demo menu). It prints the
checklist below, then: **1** selects the other person's tile (15 s to minimise the window, the
region is saved in `data\call_region.txt`), **2** runs a call session after asking whether they
are ready (15 s to bring Meet back, then 8 s grey + 90 s probe at amplitude 34, judged with
`data\thresholds-call.json`), **3** verifies the newest record.

Full setup with an attacker laptop: `docs/SWAP_SETUP.md`. In short, on the verifier laptop:

1. OBS app closed. In Meet: camera **OBS Virtual Camera**, effects off.
2. The subject pins your tile full screen. Make their tile large on your screen.
3. `.venv\Scripts\python -m lumen pick-region` → drag a box round their tile, Enter. It prints
   `screen:x,y,w,h`. Don't move the Meet window afterwards.
4. `.venv\Scripts\python -m lumen run --source "screen:x,y,w,h" --emit virtualcam --duration 60 --label CG1`
5. Calibrate from three genuine **call** sessions before trusting call verdicts.

## 7b. Deep check: 3D relief + lean (live-swap check, local probe only)

Separate from the demo and call menus. Double-click **`lumen-deepcheck.cmd`**: it asks for the
camera (0 = webcam; for a swap test, the OBS Virtual Camera index and "y" for virtual camera),
then:

1. **Genuine calibration session** (90 s, video saved). Do it three times with a real face; for a
   swap test, through the same path with the swap OFF.
2. **Calibrate** from the three newest genuine calibration sessions (`data\deep_thresholds.json`).
3. **Live check**: a normal session (L1-L3 verdict) followed by the deep check, which says SWAP
   only when both the cheek lean and the 3D relief are below the genuine range.
4. Deep-check the newest session again.

Command line: `python -m lumen.deepcheck live|calibrate|check` (`--help` on each). Reproduce the
measured results: `python experiments/deepcheck/evaluate.py --help`. Measured: docs/RESULTS.md
(promising on two FaceFusion sessions, thin margins; not validated through a video call).

## 8. The four demo scenarios

| Scenario | Command | Expected |
|---|---|---|
| Genuine | `run --duration 60 --label demo-genuine` | verify |
| Replay | `run --source "file:data\sessions\<a genuine id>\video.mkv" --duration 60 --label demo-replay` (no one at the camera) | reject (replay) |
| Lamp | `run --duration 60 --label demo-lamp` with a torch on the face from the front | abstain |
| Face swap | call mode with the attacker laptop (section 7) | reject (L3), *not yet measured* |

(Prefix each with `.venv\Scripts\python -m lumen`.) Calibrate in the demo room first (section 4).

## 9. Results and records

```powershell
.venv\Scripts\python experiments\m1_probe_survival\report.py --genuine <G ids...> <other ids...>
.venv\Scripts\python -m lumen verify data\sessions\<id>\record.json
.venv\Scripts\python -m lumen reanalyse data\sessions\<id>\record.json
```

`report.py` prints the results table (each genuine session judged by thresholds calibrated
without it). `verify` proves a record is unmodified and that the light shown matches its
one-time code. Measured numbers go into `docs/RESULTS.md` with date, place and conditions.

## 10. When something goes wrong

| Symptom | Fix |
|---|---|
| `No module named lumen` | run section 1's `pip install -e .`, and run from `C:\Users\LOQ\lumen-v2` |
| frame rate ~10 fps | more light; close other camera apps; run `smoke` again |
| "probe not detected on the neck reference" | screen brightness 100%, sit closer, full screen (not `--windowed`) |
| "data quality too low: max_gap_s" everywhere | the face is being lost: face the camera, keep still, face and neck in frame |
| "head turned further than in any calibration session" | face the screen, or recalibrate with the poses you'll use |
| "cannot open the probe output" in call mode | install OBS, open it once, close it |
| an import fails with "Application Control policy has blocked this file" | that library version is blocked on this laptop; SciPy and PyAV are pinned in requirements.txt for this reason |
| the verdict says "provisional" | no calibration in `data\thresholds.json`: section 4 |
