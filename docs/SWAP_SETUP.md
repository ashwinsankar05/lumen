# Live face-swap attacker setup (M1 session S, over a real call)

The attacker runs a live face swap on **their own laptop** and joins a Google Meet call; LUMEN on
the verifier laptop sends the probe through the call and measures the attacker's tile. Used only
to test LUMEN, only with the written consent of the person whose face is used as the source.

FaceFusion (open source, ONNX Runtime) is used because Deep-Live-Cam cannot be installed on the
dev laptop (Windows Application Control blocks the `insightface` build). Commands follow
FaceFusion's install guide as of 2026-10; if one fails, **docs.facefusion.io** is authoritative.

```
VERIFIER (dev laptop)                              ATTACKER (friend's laptop)
LUMEN --emit virtualcam -> OBS Virtual Camera ->   Meet: verifier's video FULL SCREEN, lights the face
LUMEN screen:<tile> <- Meet tile <-                webcam -> FaceFusion swap -> OBS -> OBS Virtual Camera
```

## A. Attacker laptop: install (about 30–60 min, mostly downloads)

Requirements: Windows 10/11, ideally an NVIDIA GPU (a swap below ~20 fps makes LUMEN abstain on
frame rate), ~15 GB free disk, internet for the install only.

1. **Git**: https://git-scm.com/download/win (defaults).
2. **Miniconda** (Python environments): https://docs.conda.io/en/latest/miniconda.html, install
   for "Just Me", then open **Anaconda Prompt** from the Start menu for every command below.
3. **Visual C++ Redistributable** (x64): https://aka.ms/vs/17/release/vc_redist.x64.exe
4. Create the environment and FFmpeg:
   ```
   conda create --name facefusion python=3.12 pip=25.0
   conda activate facefusion
   conda install conda-forge::ffmpeg
   ```
5. **GPU runtime** (NVIDIA only; skip for AMD/Intel):
   ```
   conda install conda-forge::cuda-runtime=12.8.1 conda-forge::cudnn=9.8.0.87
   ```
   If conda says a version is unavailable, use the versions listed on docs.facefusion.io.
6. Download and install FaceFusion:
   ```
   git clone https://github.com/facefusion/facefusion
   cd facefusion
   python install.py --onnxruntime cuda
   ```
   Use `--onnxruntime directml` for AMD/Intel GPUs, or `--onnxruntime default` for CPU only.
7. **OBS Studio**: https://obsproject.com (defaults). Open it once and close it.

## B. Attacker laptop: start the live swap

1. Anaconda Prompt:
   ```
   conda activate facefusion
   cd facefusion
   python facefusion.py run --ui-layouts webcam --open-browser
   ```
   A browser tab opens (http://127.0.0.1:7860). The first run downloads models; wait.
2. In the page:
   - **Source**: upload the consenting person's photo (sharp, frontal, well lit, one face).
   - **Processors**: `face_swapper` only (an enhancer adds latency; leave it off).
   - **Webcam**: mode `inline`, resolution `640x480`, fps `30`.
   - Press **Start**. The preview shows the swapped face. Check the frame rate stays high and
     the swap holds when the head moves slightly.
3. OBS: **Sources -> + -> Window Capture** -> pick the browser window -> right-click the source ->
   **Transform -> Edit Crop** to crop to the preview only -> **Start Virtual Camera**.
4. Close any other app that uses the webcam (FaceFusion needs it; Meet will use OBS's camera).

## C. The call

1. Both join one Google Meet.
2. Attacker: **Settings -> Video -> Camera: OBS Virtual Camera**. Turn off Meet's own effects
   (background blur, lighting adjustment, "visual effects"): they change the face's colour.
3. Verifier: camera **OBS Virtual Camera** too (LUMEN feeds it), effects off.
4. Attacker: **pin the verifier's tile and make it full screen**; screen brightness 100%; sit
   ~40 cm away, facing the screen, face and neck visible to the webcam; keep still.
5. Verifier: make the attacker's tile large on screen, then
   `python -m lumen pick-region` and drag a box round the attacker's tile.

## D. Sessions (60 s each, one "ready? / yes" before each)

Calibrate **through this call**; thresholds from the local sessions do not transfer.

| Label | Attacker laptop | Expected |
|---|---|---|
| CG1–CG3 | FaceFusion **stopped**; OBS shows the plain webcam (add a Video Capture Device source) | verify -> calibrate from these three |
| CS1 | FaceFusion **running** (swap on) | reject (L3: no 3D cheek lean) |
| CG4 | FaceFusion stopped again | verify (nothing drifted) |

Verifier command for each: `python -m lumen run --source "screen:<x>,<y>,<w>,<h>" --emit virtualcam --duration 60 --label CG1`.
Write down: call app, both laptops, the attacker's GPU and swap fps, the tile size in pixels,
the source photo's owner (consent), and whether calibration found the video mirrored.
