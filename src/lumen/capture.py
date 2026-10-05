"""Frame sources with per-frame timestamps on one shared monotonic clock.

Sources (open_source spec strings):
  camera:<index>          a webcam, or a virtual camera (OBS, a face-swap tool) — the same to us
  file:<path>             a recording played back in real time, as a virtual camera would (replay drill)
  screen:<x>,<y>,<w>,<h>  a screen rectangle, e.g. the subject's tile in a video call
  mjpeg:<url>             a local MJPEG stream; an X-Capture-Time header per part, if present,
                          is used as the frame's timestamp

A FrameGrabber thread does nothing but read and timestamp, so timestamps stay honest when
processing falls behind: slow processing drops frames (and counts them) instead of letting frames
queue inside the driver and come out with compressed, wrong timestamps.

Exposure and white balance (PROJECT.md §8). Many drivers cannot report their live auto values, so
"freeze the current value" can freeze a stale default. `lock()` instead measures the auto-settled
image, steps through manual settings and keeps the closest match, or stays on auto and says so.
`close()` restores auto modes, because drivers keep them after the camera is released.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import cv2
import numpy as np

from lumen.colour import bgr8_to_lab

clock = time.perf_counter  # the one clock shared by emitter, capture and analysis


@dataclass
class Frame:
    t: float
    image: np.ndarray  # BGR uint8
    index: int


@dataclass
class CaptureReport:
    source: str
    width: int = 0
    height: int = 0
    fps_requested: float = 0.0
    fps_reported: float = 0.0
    exposure_locked: bool | None = None  # None: not applicable, or not attempted
    white_balance_locked: bool | None = None
    simulated: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Source(Protocol):
    report: CaptureReport

    def read(self) -> np.ndarray | None:
        """Block until the next frame. None on a failed read; raise EOFError at end of stream."""

    def lock(self) -> None: ...

    def close(self) -> None: ...


class _Paced:
    """Sleep so successive calls happen at a fixed rate on `clock`."""

    def __init__(self, fps: float, now: Callable[[], float] = clock):
        self.period = 1.0 / fps
        self.now = now
        self._next = now()

    def wait(self) -> None:
        delay = self._next - self.now()
        if delay > 0:
            time.sleep(delay)
        self._next = max(self._next + self.period, self.now())


def exposure_steps_for(fps: float) -> int:
    """Longest DirectShow exposure step (log2 seconds) that fits in one frame at `fps`."""
    return int(np.floor(np.log2(1.0 / fps)))


class CameraSource:
    # OpenCV's DirectShow convention for CAP_PROP_AUTO_EXPOSURE.
    AUTO_ON, AUTO_OFF = 0.75, 0.25
    WB_TEMPERATURES = range(2800, 7000, 700)
    MAX_LIGHTNESS_ERROR = 10.0  # L*: a steady exposure matters more than an exact match
    MAX_CHROMA_ERROR = 3.0

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: float = 30.0):
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == "win32" else [cv2.CAP_ANY]
        self.cap = None
        for backend in backends:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                self.cap = cap
                break
            cap.release()
        if self.cap is None:
            raise RuntimeError(f"cannot open camera {index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._restore_exposure = self._restore_wb = False
        # DirectShow exposure is log2(seconds). Only exposures that fit in one frame period are
        # tried: a longer one caps the frame rate, and drivers keep it after release (measured on
        # the dev laptop: a 125 ms trial left the camera at 10 fps, even back on auto).
        self.exposures = list(range(-9, exposure_steps_for(fps) + 1))
        self.report = CaptureReport(
            source=f"camera:{index} ({self.cap.getBackendName()})",
            width=int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps_requested=fps,
            fps_reported=float(self.cap.get(cv2.CAP_PROP_FPS)),
        )

    def read(self) -> np.ndarray | None:
        ok, img = self.cap.read()
        return img if ok else None

    def _auto_exposure(self) -> None:
        """Back to auto exposure from a short manual exposure, so auto starts from a fast frame
        rate (on the dev laptop's driver, auto keeps a long exposure it was switched on from)."""
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.AUTO_OFF)
        self.cap.set(cv2.CAP_PROP_EXPOSURE, self.exposures[-1])
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.AUTO_ON)

    def _measure(self, settle: int = 2, n: int = 2) -> np.ndarray | None:
        """Mean CIELAB of the next few frames, after letting a setting take effect."""
        for _ in range(settle):
            self.cap.read()
        means = []
        for _ in range(n):
            ok, img = self.cap.read()
            if ok:
                means.append(cv2.resize(img, (80, 60), interpolation=cv2.INTER_AREA).reshape(-1, 3).mean(axis=0))
        return bgr8_to_lab(np.mean(means, axis=0)) if means else None

    def lock(self) -> None:
        cap, notes = self.cap, self.report.notes
        target = self._measure()
        if target is None:
            notes.append("lock skipped: no frames to measure")
            return

        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.AUTO_OFF)
        self._restore_exposure = True
        trials = []
        for e in self.exposures:
            cap.set(cv2.CAP_PROP_EXPOSURE, e)
            if (lab := self._measure()) is not None:
                trials.append((abs(lab[0] - target[0]), e, lab[0]))
        best = min(trials) if trials else None
        if best is not None and best[0] <= self.MAX_LIGHTNESS_ERROR:
            cap.set(cv2.CAP_PROP_EXPOSURE, best[1])
            self.report.exposure_locked = True
            notes.append(f"exposure locked at {best[1]}: L* {best[2]:.1f} vs auto {target[0]:.1f}")
        else:
            self._auto_exposure()
            self._restore_exposure = False
            self.report.exposure_locked = False
            gap = "no frames" if best is None else f"closest L* off by {best[0]:.1f}"
            notes.append(f"exposure NOT locked ({gap}); left on auto, relying on the neck reference")

        wb_target = self._measure()
        cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        self._restore_wb = True
        trials = []
        for temp in self.WB_TEMPERATURES:
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE, temp)
            if (lab := self._measure()) is not None and wb_target is not None:
                trials.append((float(np.hypot(*(lab[1:] - wb_target[1:]))), temp))
        best = min(trials) if trials else None
        if best is not None and best[0] <= self.MAX_CHROMA_ERROR:
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE, best[1])
            self.report.white_balance_locked = True
            notes.append(f"white balance locked at {best[1]} K: chroma within {best[0]:.1f} of auto")
        else:
            cap.set(cv2.CAP_PROP_AUTO_WB, 1)
            self._restore_wb = False
            self.report.white_balance_locked = False
            gap = "no frames" if best is None else f"closest chroma off by {best[0]:.1f}"
            notes.append(f"white balance NOT locked ({gap}); left on auto, relying on the neck reference")

    def close(self) -> None:
        """Release the camera, putting back any auto mode lock() turned off (drivers persist it)."""
        if self._restore_exposure:
            self._auto_exposure()
        if self._restore_wb:
            self.cap.set(cv2.CAP_PROP_AUTO_WB, 1)
        self.cap.release()


class VideoFileSource:
    """A recording delivered in real time, like a virtual camera playing it.

    If `video_timestamps.csv` sits next to the video (sessions saved with --save-video write it),
    frames are released at their originally captured intervals: a webcam that delivered 20 fps
    is replayed at 20 fps even though the container is stamped 30. Otherwise the container's fps
    is used.
    """

    def __init__(self, path: str | Path, paced: bool = True):
        path = Path(path)
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video {path}")
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS)) or 30.0
        self.paced = paced
        self._start: float | None = None
        self._n = 0
        self._offsets: np.ndarray | None = None
        notes = ["recorded video: exposure and white balance are whatever the recording had"]
        stamps = path.with_name("video_timestamps.csv")
        if stamps.exists():
            t = np.loadtxt(stamps, delimiter=",", ndmin=1)
            if len(t) > 1 and np.all(np.diff(t) > 0):
                self._offsets = t - t[0]
                self.fps = (len(t) - 1) / (t[-1] - t[0])
                notes.append(f"replayed at the recorded frame times ({self.fps:.1f} fps)")
        self.report = CaptureReport(
            source=f"file:{path}",
            width=int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps_requested=self.fps, fps_reported=self.fps, notes=notes,
        )

    def _due(self, n: int) -> float:
        if self._offsets is not None and n < len(self._offsets):
            return float(self._offsets[n])
        return n / self.fps

    def read(self) -> np.ndarray | None:
        ok, img = self.cap.read()
        if not ok:
            raise EOFError
        if self.paced:
            if self._start is None:
                self._start = clock()
            delay = self._start + self._due(self._n) - clock()
            if delay > 0:
                time.sleep(delay)
        self._n += 1
        return img

    def lock(self) -> None:
        pass

    def close(self) -> None:
        self.cap.release()


class ScreenSource:
    """A rectangle of the screen (e.g. the subject's video tile), grabbed at a fixed rate."""

    def __init__(self, x: int, y: int, w: int, h: int, fps: float = 30.0):
        import mss  # only needed for this source

        self._mss = mss.mss()
        self._rect = {"left": x, "top": y, "width": w, "height": h}
        self._pace = _Paced(fps)
        self.report = CaptureReport(
            source=f"screen:{x},{y},{w},{h}", width=w, height=h, fps_requested=fps, fps_reported=fps,
            notes=["screen capture: exposure and white balance belong to the remote camera; "
                   "the neck reference cancels drift"],
        )

    def read(self) -> np.ndarray | None:
        self._pace.wait()
        return cv2.cvtColor(np.asarray(self._mss.grab(self._rect)), cv2.COLOR_BGRA2BGR)

    def lock(self) -> None:
        pass

    def close(self) -> None:
        self._mss.close()


class MjpegSource:
    """A local multipart MJPEG stream, read like a camera.

    If a part carries X-Capture-Time (seconds on this machine's perf_counter, which on Windows is
    system-wide), that becomes the frame's timestamp instead of its arrival time, so bursty
    delivery does not distort timing.
    """

    def __init__(self, url: str, timeout: float = 5.0):
        import urllib.request

        self._resp = urllib.request.urlopen(url, timeout=timeout)
        ctype = self._resp.headers.get("Content-Type", "")
        if "boundary=" not in ctype:
            raise RuntimeError(f"{url} is not a multipart MJPEG stream ({ctype!r})")
        self._boundary = ("--" + ctype.split("boundary=", 1)[1].strip().strip('"')).encode()
        self.last_frame_time: float | None = None
        self.report = CaptureReport(source=f"mjpeg:{url}", fps_requested=30.0,
                                    notes=["MJPEG stream: exposure and white balance belong to the producer"])

    def read(self) -> np.ndarray | None:
        r = self._resp
        line = r.readline()
        while line and line.strip() != self._boundary:
            line = r.readline()
        if not line:
            raise EOFError
        headers = {}
        while (line := r.readline().strip()):
            k, _, v = line.decode(errors="replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        data = r.read(int(headers.get("content-length", "0")))
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        self.last_frame_time = float(headers["x-capture-time"]) if "x-capture-time" in headers else None
        if img is not None and not self.report.width:
            self.report.height, self.report.width = img.shape[:2]
        return img

    def lock(self) -> None:
        pass

    def close(self) -> None:
        self._resp.close()


def roi_to_spec(roi: tuple[int, int, int, int], monitor: dict, scale: float) -> str | None:
    """A rectangle drawn on a (scaled) screenshot -> the absolute 'screen:x,y,w,h' source spec."""
    x, y, w, h = roi
    if w <= 0 or h <= 0:
        return None
    return (f"screen:{monitor['left'] + round(x / scale)},{monitor['top'] + round(y / scale)},"
            f"{round(w / scale)},{round(h / scale)}")


def pick_screen_region(monitor_index: int = 1, max_width: int = 1600) -> str | None:
    """Screenshot a monitor, let the operator drag a box round the subject's video tile.

    Returns the source spec for that tile, or None if the selection was cancelled.
    """
    import mss

    with mss.mss() as grabber:
        monitor = grabber.monitors[monitor_index]
        shot = cv2.cvtColor(np.asarray(grabber.grab(monitor)), cv2.COLOR_BGRA2BGR)
    scale = min(1.0, max_width / shot.shape[1])
    view = cv2.resize(shot, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else shot
    title = "Drag a box round the subject's video tile, then press Enter (Esc cancels)"
    roi = cv2.selectROI(title, view, showCrosshair=False)
    cv2.destroyWindow(title)
    return roi_to_spec(tuple(int(v) for v in roi), monitor, scale)


def open_source(spec: str, *, width: int = 640, height: int = 480, fps: float = 30.0) -> Source:
    kind, _, arg = spec.partition(":")
    if kind == "camera":
        return CameraSource(int(arg or 0), width, height, fps)
    if kind == "file":
        return VideoFileSource(arg)
    if kind == "mjpeg":
        return MjpegSource(arg)
    if kind == "screen":
        x, y, w, h = (int(v) for v in arg.split(","))
        return ScreenSource(x, y, w, h, fps)
    raise ValueError(f"unknown source {spec!r}; use camera:<n>, file:<path>, mjpeg:<url> or "
                     "screen:<x>,<y>,<w>,<h>")


class FrameGrabber:
    """Reads a source on its own thread and timestamps each frame the moment it arrives.

    `frames` holds the most recent frames; when the consumer falls behind, the oldest are dropped
    and counted. After `max_failures` consecutive failed reads (camera unplugged) `error` is set
    and the grabber stops. The end of a file source sets `ended`.
    """

    def __init__(self, source: Source, *, queue_size: int = 8, max_failures: int = 30,
                 now: Callable[[], float] = clock):
        self.source = source
        self.frames: queue.Queue[Frame] = queue.Queue(maxsize=queue_size)
        self.max_failures = max_failures
        self.now = now
        self.error: str | None = None
        self.ended = False
        self.dropped = 0
        self.count = 0
        self.locked_at: float | None = None
        self._recent: deque[float] = deque(maxlen=120)
        self._stop = threading.Event()
        self._lock_requested = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lumen-grabber", daemon=True)

    def start(self) -> "FrameGrabber":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def request_lock(self) -> None:
        """Lock exposure/WB on the grabber thread, between reads (VideoCapture is not thread-safe)."""
        self._lock_requested.set()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def fps(self) -> float:
        """Measured frame rate over the most recent frames."""
        r = list(self._recent)
        return (len(r) - 1) / (r[-1] - r[0]) if len(r) > 1 and r[-1] > r[0] else 0.0

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if self._lock_requested.is_set():
                self._lock_requested.clear()
                try:
                    self.source.lock()
                except Exception as exc:  # report it; never crash the capture over a lock
                    self.source.report.notes.append(f"lock failed: {exc!r}")
                self.locked_at = self.now()
            try:
                img = self.source.read()
                last = "read returned no frame"
            except EOFError:
                self.ended = True
                return
            except Exception as exc:  # driver errors when a device disappears
                img, last = None, repr(exc)
            t = self.now()
            stamped = getattr(self.source, "last_frame_time", None)
            if stamped is not None:
                t = stamped
            if img is None:
                failures += 1
                if failures >= self.max_failures:
                    self.error = f"capture lost: {failures} consecutive failed reads ({last})"
                    return
                time.sleep(0.005)
                continue
            failures = 0
            frame = Frame(t=t, image=img, index=self.count)
            self.count += 1
            self._recent.append(t)
            while True:
                try:
                    self.frames.put_nowait(frame)
                    break
                except queue.Full:
                    try:
                        self.frames.get_nowait()
                        self.dropped += 1
                    except queue.Empty:
                        pass
