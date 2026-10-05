"""SIMULATED scenes for tests and for exercising the console without a camera.

Nothing produced here is a measurement. Every capture report from a synthetic source has
`simulated=True` and says so in its notes, and records carry that through (PROJECT.md §4).

A scene is a flat-shaded head at fixed landmarks: wall, neck, face and face sub-regions, each with
a base colour and its own response to the probe: a gain, a rotation of the chroma axes (the
camera's axes are not the display's), and for a two-sided probe the share of light it takes
from each screen half. Per-pixel sensor noise and per-frame region noise stand in for camera and
codec noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from lumen.capture import CaptureReport, _Paced, clock
from lumen.colour import lab_to_srgb
from lumen.probe import Probe
from lumen.regions import Landmarks, region_masks

SIZE = (240, 320)  # h, w
LANDMARKS = Landmarks(
    box=np.array([125.0, 30.0, 70.0, 100.0]),
    points=np.array([[140.0, 70.0], [180.0, 70.0], [160.0, 90.0], [146.0, 114.0], [174.0, 114.0]]),
    score=0.99,
)

# Share of each region's light from the (left, right) screen half. Names are image-based and the
# camera is unmirrored, so the image-left cheek is the subject's right cheek, facing the right half.
REAL_3D = {"face.cheek_l": (0.35, 0.65), "face.cheek_r": (0.65, 0.35)}
FLAT = {}  # every region takes the screen average: what a 2D re-render of the face would do


@dataclass(frozen=True)
class Scenario:
    description: str
    face: float  # face gain (Lab units per probe unit)
    neck: float
    wall: float
    lateral: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(REAL_3D))
    follows: str = "probe"     # "probe": today's probe; "other": an earlier session's (replay)
    face_lightness: float = 65.0


SCENARIOS = {
    "genuine": Scenario("a real subject", face=0.09, neck=0.06, wall=0.005),
    "swap_unlit": Scenario("face re-rendered without the probe", face=0.0, neck=0.06, wall=0.005, lateral=FLAT),
    "swap_inherit": Scenario("face re-rendered from the lit frame: overall probe kept, no 3D lean",
                             face=0.09, neck=0.06, wall=0.005, lateral=FLAT),
    "replay": Scenario("a recording of an earlier session", face=0.09, neck=0.06, wall=0.005, follows="other"),
    "lamp": Scenario("a desk lamp on the face dilutes the probe there", face=0.03, neck=0.06, wall=0.005,
                     face_lightness=82.0),
    "far": Scenario("subject far from the screen: almost no probe light", face=0.002, neck=0.0015, wall=0.0),
    "relit": Scenario("whole frame relit uniformly", face=0.06, neck=0.06, wall=0.06, lateral=FLAT),
}

_BASE_LAB = {"wall": (75.0, 0.0, 5.0), "neck": (60.0, 13.0, 16.0), "face": (65.0, 15.0, 18.0)}
_ROTATION = {"wall": 0.1, "neck": 0.5, "face": 0.6}


def _rotation(theta: float) -> np.ndarray:
    return np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])


class SyntheticScene:
    """Renders the scenario at a given clock time, lit by the probe emitted `delay_s` earlier.

    `probe_t0` returns (probe, clock time of probe time zero); it is a callable because a session
    fixes t0 only when it starts.
    """

    def __init__(self, scenario: str | Scenario, probe_t0: Callable[[], tuple[Probe, float]], *,
                 delay_s: float = 0.3, pixel_noise: float = 2.0, region_noise: float = 0.25, seed: int = 0):
        self.scenario = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
        self.name = scenario if isinstance(scenario, str) else "custom"
        self.probe_t0 = probe_t0
        self.delay_s = delay_s
        self.pixel_noise = pixel_noise
        self.region_noise = region_noise
        self.rng = np.random.default_rng(seed)
        self._earlier: Probe | None = None
        # A label image: each pixel's region index, later regions painted over earlier ones.
        masks = region_masks(LANDMARKS, SIZE)
        self._regions = ["wall", "neck", "face"] + [n for n in masks if n.startswith("face.")]
        self._labels = np.zeros(SIZE, np.intp)
        for i, name in enumerate(self._regions[1:], start=1):
            self._labels[masks[name] > 0] = i
        # Sensor noise from a bank of frames: drawing 230k normals per frame dominated the cost,
        # and over large regions pixel noise averages out (region_noise models what survives).
        self._noise = pixel_noise * self.rng.standard_normal((32,) + SIZE + (3,), dtype=np.float32)

    def _drive(self, t: float) -> np.ndarray:
        probe, t0 = self.probe_t0()
        pt = t - self.delay_s - t0
        if self.scenario.follows == "other":
            if self._earlier is None:
                self._earlier = Probe("synthetic-earlier-session", layout=probe.layout,
                                      duration_s=600.0, amplitude=probe.amplitude)
            return self._earlier.chroma_at(pt % 600.0) if pt >= 0 else np.zeros(probe.n_channels)
        if 0 <= pt <= probe.duration_s:
            return probe.chroma_at(pt)
        return np.zeros(probe.n_channels)

    def _lab(self, region: str, drive: np.ndarray) -> np.ndarray:
        s = self.scenario
        kind = "face" if region.startswith("face") else region
        gain = {"face": s.face, "neck": s.neck, "wall": s.wall}[kind]
        if drive.size == 4:  # two-sided probe: this region's share of each half's light
            w_left, w_right = s.lateral.get(region, (0.5, 0.5))
            drive = w_left * drive[:2] + w_right * drive[2:]
        lab = np.array(_BASE_LAB[kind], dtype=float)
        if kind == "face":
            lab[0] = s.face_lightness
        lab[1:] += gain * (_rotation(_ROTATION[kind]) @ drive) + self.region_noise * self.rng.standard_normal(2)
        return lab

    def render(self, t: float) -> np.ndarray:
        """BGR uint8 frame at clock time t."""
        drive = self._drive(t)
        lab = np.array([self._lab(name, drive) for name in self._regions])
        lut = (lab_to_srgb(lab)[:, ::-1] * 255).astype(np.float32)  # region index -> BGR
        img = lut[self._labels]
        img += self._noise[self.rng.integers(len(self._noise))]
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)


class SyntheticSource:
    """Frames from a SyntheticScene at a steady rate, rendered for the moment they are read."""

    def __init__(self, scene: SyntheticScene, fps: float = 30.0, now: Callable[[], float] = clock):
        self.scene = scene
        self.now = now
        self._pace = _Paced(fps, now)
        self.report = CaptureReport(source=f"synthetic:{scene.name}", width=SIZE[1], height=SIZE[0],
                                    fps_requested=fps, fps_reported=fps, simulated=True,
                                    notes=[f"SIMULATED scene ({scene.scenario.description}): not a measurement"])

    def read(self) -> np.ndarray:
        self._pace.wait()
        return self.scene.render(self.now())

    def lock(self) -> None:
        pass

    def close(self) -> None:
        pass


class FixedDetector:
    """Detector stand-in that always reports the synthetic scene's landmarks."""

    def __init__(self, landmarks: Landmarks = LANDMARKS):
        self.landmarks = landmarks

    def detect(self, bgr: np.ndarray) -> Landmarks | None:
        return self.landmarks.scaled(bgr.shape[1] / SIZE[1])
