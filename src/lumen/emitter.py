"""Render the probe full-screen (or in a window) and log what was emitted, frame by frame.

Before probe time zero the emitter shows the probe's base colour, so the camera's auto-exposure
can settle on the lit scene before it is locked. Those frames are not logged.

Timestamps are taken immediately before each frame is handed to the display. The remaining delay
until light leaves the panel is roughly constant and is absorbed, with the rest of the pipeline
delay, into the lag estimated from the neck (features.estimate_lag).

8-bit output: a faint probe moves each RGB channel by only a few code values. The field is
ordered-dithered with a fixed 8x8 Bayer pattern, so the *average* colour over the screen, which
is what lights the face, keeps sub-code-value precision without adding temporal noise.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Protocol

import cv2
import numpy as np

from lumen.capture import clock
from lumen.colour import lab_to_srgb
from lumen.probe import EmissionLog

FIELD_SHAPE = (90, 160)  # rendered small; the window scales it up


def bayer(n: int) -> np.ndarray:
    """n x n Bayer index matrix (n a power of two), values 0 .. n*n - 1."""
    m = np.array([[0, 2], [3, 1]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return m


_BAYER8 = (bayer(8) + 0.5) / 64.0


def _dither(shape: tuple[int, int]) -> np.ndarray:
    reps = (shape[0] // 8 + 1, shape[1] // 8 + 1)
    return np.tile(_BAYER8, reps)[: shape[0], : shape[1]]


_DITHER = _dither(FIELD_SHAPE)


def render_field(rgb, shape: tuple[int, int] = FIELD_SHAPE) -> np.ndarray:
    """sRGB in [0, 1] -> dithered BGR uint8 field whose mean is rgb * 255 to within 1/64.

    rgb of shape (3,) fills the field; (2, 3) fills the left and right halves (split probe).
    """
    rgb = np.asarray(rgb, dtype=float)
    dither = _DITHER if shape == FIELD_SHAPE else _dither(shape)
    colour = np.empty(shape + (3,))
    if rgb.ndim == 1:
        colour[:] = rgb[::-1]
    else:
        half = shape[1] // 2
        colour[:, :half] = rgb[0][::-1]
        colour[:, half:] = rgb[1][::-1]
    return np.clip(np.floor(colour * 255.0 + dither[..., None]), 0, 255).astype(np.uint8)


class Display(Protocol):
    def show(self, bgr: np.ndarray) -> int:
        """Present a frame; return a pressed key code or -1."""

    def close(self) -> None: ...


class CvDisplay:
    """OpenCV window, full-screen by default and kept above other windows. Esc aborts.

    A probe window that opens behind the terminal does not light the subject at all, so after the
    first frame the window is made always-on-top and brought forward, and `notes` records whether
    that worked (sessions copy the notes into their record).
    """

    def __init__(self, name: str = "LUMEN probe", *, fullscreen: bool = True,
                 size: tuple[int, int] = (1280, 720), position: tuple[int, int] = (0, 0)):
        self.name = name
        self.notes: list[str] = []
        self._raised = False
        cv2.namedWindow(name, cv2.WINDOW_NORMAL)
        cv2.moveWindow(name, *position)
        if fullscreen:
            cv2.setWindowProperty(name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        else:
            cv2.resizeWindow(name, *size)

    def show(self, bgr: np.ndarray) -> int:
        cv2.imshow(self.name, bgr)
        if not self._raised:  # the native window exists only after the first imshow
            self._raised = True
            self._bring_to_front()
        key = cv2.waitKey(1)
        return -1 if key < 0 else key & 0xFF

    def _bring_to_front(self) -> None:
        try:
            cv2.setWindowProperty(self.name, cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            self.notes.append("probe window: OpenCV could not set always-on-top")
        if sys.platform != "win32":
            return
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, self.name)
        if not hwnd:
            self.notes.append("probe window: native window not found")
            return
        HWND_TOPMOST, SWP_NOSIZE_NOMOVE_SHOW = -1, 0x0001 | 0x0002 | 0x0040
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE_NOMOVE_SHOW)
        user32.SetForegroundWindow(hwnd)
        cv2.waitKey(1)
        topmost = bool(user32.GetWindowLongW(hwnd, -20) & 0x00000008)  # GWL_EXSTYLE, WS_EX_TOPMOST
        self.notes.append(f"probe window: always-on-top {topmost}, "
                          f"foreground {user32.GetForegroundWindow() == hwnd}")

    def close(self) -> None:
        try:
            cv2.destroyWindow(self.name)
            cv2.waitKey(1)
        except cv2.error:
            pass


class Emitter:
    """Shows the base colour until the log's t0, then the probe, until it ends."""

    def __init__(self, log: EmissionLog, display: Display, *, fps: float = 60.0,
                 now: Callable[[], float] = clock):
        self.log = log
        self.display = display
        self.period = 1.0 / fps
        self.now = now
        self.base_rgb = lab_to_srgb(log.probe.base_lab())
        self.end = log.t0 + log.probe.duration_s

    def run(self, stop: threading.Event | None = None) -> str:
        """Run until the probe ends ("done"), `stop` is set ("stopped") or Esc ("escape")."""
        next_frame = self.now()
        while True:
            if stop is not None and stop.is_set():
                return "stopped"
            t = self.now()
            if t >= self.end:
                return "done"
            rgb = self.base_rgb if t < self.log.t0 else self.log.emit(t)
            if self.display.show(render_field(rgb)) == 27:
                return "escape"
            next_frame += self.period
            delay = next_frame - self.now()
            if delay > 0:
                time.sleep(delay)
            else:  # fell behind: do not try to catch up with a burst of frames
                next_frame = self.now()
