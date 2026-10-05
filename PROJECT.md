# LUMEN — proof of physical presence for live video

Hackathena (Jyothi Engineering College), theme: Detection and Prevention of AI-Based Frauds.
Team Owlight — Alen J Edakulathur (JEC24CS027), Ashwin Sankar K R (JEC24CS062).

This file is the project's standing context. Read it before proposing work.

---

## 1. What we are building, in one paragraph

A real-time face swap delivered through a virtual camera is accepted by Zoom, Meet, Teams and
many video-KYC portals as an ordinary webcam. LUMEN decides whether the person on the other end
of a video call is physically present, **by measurement rather than by appearance**. The
verifier's own outgoing video is modulated with a faint, pseudorandom, constant-brightness colour
pattern (2–8 Hz) seeded by a one-time code, with **independent sequences on the left and right
halves of the screen**. That light falls on the subject. LUMEN then measures how the returning
video responds: the **face** against the **neck** (same light, same distance, same camera and
codec), and each **cheek** against the half of the screen it faces. A real face is a 3D object in
front of the screen, so each cheek leans towards the sequence of the half it faces. A face-swap
re-renders the face as a 2D image and has no physical reason to reproduce that lean (hypothesis,
to be measured in M1). A replay follows an old code. A fully synthetic frame barely responds.

## 2. The thesis (do not drift from this)

Passive deepfake classifiers lose, because they ask "does this look synthetic?" and every new
generator is optimised to beat exactly that question. Physics does not change when a new
generator ships. **We never build a deepfake appearance classifier.** If a proposed feature
amounts to "train a model to recognise fake faces", it is out of scope.

## 3. The three measurement layers

| Layer | What it asks | Stops |
|---|---|---|
| L1 Coherence | Does the face's colour track the emitted probe as well as the neck does? (multiple coherence in the probe band) | Unlit swaps, whole-frame fakes, replays |
| L2 Differential gain | Do face, neck and background respond in physically possible proportions? Light falls off steeply with distance | Attackers who relight their output uniformly |
| L3 Lateral response | Do the cheeks lean towards the halves of the screen they face, as much as a real face's do? | Generators that inherit the probe from the camera frame (to be measured in M1) |

The neck is the reference channel, not the background: it is real skin, at nearly the same
distance, in the same camera, codec and network path, and a face-swap has no reason to touch it.
It also survives a virtual background.

**The verdict is three-valued: verify / reject / abstain.** Abstain when probe SNR is too low
(bright ambient light, subject too far, verifier's video too small on their screen), when a lamp
lights the face more than in any calibration session, or when the head is turned further than in
calibration. Abstaining is a feature; never guess to avoid it.

## 4. Status (started 2026-10-05)

Built from scratch starting 2026-10-05. **Nothing has been measured yet**: every test runs on
synthetic signals with known ground truth. `docs/RESULTS.md` is empty until M1 is run.

Design decisions:

1. The two-sided probe is the **default** layout; a single-screen probe stays available
   (`--single`) for comparison runs. Rationale: a generator that renders each output face from
   the current camera frame inherits that frame's overall colour, probe included, so a single
   whole-screen probe may pass straight through it. Light from two independent halves of the
   screen reaches each cheek in proportions set by 3D geometry, which a 2D re-render has no
   reason to reproduce. M1 must test this, not assume it.
2. L3 is the lateral cheek contrast, with its threshold calibrated from genuine sessions only.
3. Per-sub-region gain maps ("spatial field") are reported as diagnostics, not enforced: they
   depend on room lighting, whereas the lateral contrast is referenced to the screen itself.

**Rule: never write a simulated number anywhere that implies it was measured.** Measured results go
in `docs/RESULTS.md` with the date, hardware and conditions. Keep the two apart in every document,
commit message and slide.

## 5. Milestones

**M1 — Does the physics hold?** Do this first; see `docs/PROTOCOL.md`. Genuine vs live swap vs
replay vs lamp, same room and light, single and split probe. Report the numbers either way; a
negative result redirects the project, it does not end it.

**M2 — Detector core.** Probe generator (nonce → sequences), emitter, capture, region segmentation,
feature extraction, L1–L3, verdict with abstain. Target ≥25 fps on a laptop CPU.

**M3 — Operator console.** FastAPI service plus a single-page UI: live traces for probe / neck /
face, the three measurements, verdict, and a signed session record.

**M4 — Demo hardening.** The four demo scenarios; failure drills (no network, bad light, camera
unplugged); replicate the lateral result with a second subject and the probe carried through a
real video call and its codec.

## 6. Stack and constraints

Python, NumPy, SciPy (Welch coherence, cross-spectral density), OpenCV (capture, display, and the
YuNet face detector → face/neck/background ROIs), FastAPI, `cryptography` (Ed25519 records),
plain HTML/JS console.

The dev laptop runs Windows Application Control, which blocks some native libraries (MediaPipe;
SciPy 1.18's pythran extension). `regions.py` therefore uses OpenCV's YuNet (box + 5 points, model
committed under `models/`); five points suffice because regions only need to sit on skin and be
large. Check that any new native dependency imports on the dev laptop before relying on it.

- Runs on a laptop **CPU**. The GPU is only for running the attack (a live face-swap tool) in demos.
- No training data and no model training. The thresholds come from genuine captures, not from
  deepfake examples.
- No cloud API in the live path. The demo must work with the network cable pulled out.
- Keep modules small and independently testable; test signal code against synthetic signals with
  known ground truth before touching a webcam.

## 7. Repository layout

```
src/lumen/
  colour.py      sRGB <-> CIELAB
  probe.py       nonce -> deterministic isoluminant colour sequence(s) + emission log
  emitter.py     renders the probe (full-screen / window), dithered, logs each presented frame
  capture.py     camera / video-file / screen / MJPEG sources; grabber thread timestamps frames
  regions.py     YuNet -> face, neck, background ROIs and face sub-regions; head pose
  features.py    per-region chroma series -> coherence, differential gain, lateral index
  decide.py      thresholds -> verify / reject / abstain, with SNR gate; calibration
  record.py      signed session record (what was emitted, what returned)
  session.py     one session end to end (warm-up, exposure lock, analysis, record)
  api.py         FastAPI service
  synthetic.py   SIMULATED scenes for tests/console only (records say so)
console/         single-page operator UI (no CDN)
models/          YuNet ONNX model (committed, so the demo needs no network)
experiments/     m1_probe_survival/, lateral_probe/, codec_survival/; each writes its own results
docs/            RESULTS.md (measured only), PROTOCOL.md, DEMO.md
tests/           synthetic-signal tests, pipeline, session, API
data/            recordings, keys, thresholds (git-ignored)
```

## 8. Measurement conventions

- Work in a chroma space, not RGB brightness: CIELAB (a*, b*). The probe is isoluminant by
  design, so the signal lives in chroma, not luminance.
- Log the emitted probe value **per frame with a timestamp**, and the captured frames with their
  own timestamps. Align by timestamp, never by frame index.
- Disable webcam auto-exposure and auto white balance where the driver allows. Where it does not
  (the dev laptop's camera), rely on the neck reference to cancel common-mode drift, and say so.
- Coherence: Welch segments (multiple coherence on all probe chroma channels; equals
  `scipy.signal.coherence` for one channel, tested). Report the number of segments K, because
  the noise floor is roughly p/K (p = probe channels: 2 single, 4 two-sided). Always report the
  band used (2–8 Hz).
- Pipeline delay: estimate it from the neck (band-passed first; slow drift otherwise hides the
  peak) and apply it to every region. Never optimise the face over lag.
- Always record the genuine baseline in the same session and same lighting as the attack run.
  Separation between the two is the result; an absolute number alone is not.

## 9. Known limits, stated honestly

- Works best when the verifier's video is large on the subject's screen; a small tile means less
  light and a weaker probe. The answer is to abstain, not to guess.
- Very bright ambient light reduces SNR.
- Video codecs may attenuate subtle chroma modulation. Mitigation: average over large regions.
- Head turns change the genuine lateral contrast (one cheek faces the screen more); the pose gate
  abstains beyond the calibrated range rather than risk a false reject.
- An attacker who performs real-time inverse rendering of skin under an illuminant they cannot
  predict would defeat all three layers. There is no known implementation. Say it this way; do not
  claim the system is unbeatable.

## 10. Prior work — cite accurately, never claim we invented active liveness

Re-check every citation against the paper before it goes on a slide; add numbers only from the
source itself.

- **GOTCHA**, arXiv 2210.06186: challenge-response for real-time deepfakes; asks the subject to
  perform tasks and scores appearance degradation, so it needs subject cooperation.
- **Face Flashing**, NDSS 2018 (arXiv 1801.01949): screen light + timing, but against photo/video
  replay, not generative swaps, and no within-subject differential.
- **iProov Flashmark**: commercial, patented; one-time colour sequence scored in the cloud; requires
  their SDK on the subject's device.
- **Injection attack detection** (CyberLink, Microblink, Regula): inspects the capture path (virtual
  cameras, hooked APIs, emulators); needs an SDK on the subject's device, so it cannot protect an
  ordinary call.
- **Passive classifiers and rPPG detectors**: known to generalise poorly to unseen generators;
  cite a specific cross-dataset evaluation before quoting a number.

Our narrow claim: a zero-cooperation, differential photometric probe carried by an ordinary video
call and scored on physical quantities rather than perceived quality.

## 11. Demo scenarios (M4)

1. **Baseline** — a judge on camera; face and neck follow the probe, cheeks lean; verified.
2. **Live swap** — with that judge's permission, their face worn through a live face-swap tool; rejected (L3).
3. **Replay** — a recording of a genuine session; the one-time pattern does not match; rejected.
4. **Abstain** — a desk lamp floods the face; the system refuses to answer.

## 12. Working agreement

- Ask before adding a dependency that needs a GPU or a network call in the live path.
- Prefer one measured number over three paragraphs of explanation.
- When something fails to reproduce, write it in `docs/RESULTS.md` rather than quietly retuning
  until it passes.
