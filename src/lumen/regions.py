"""Face landmarks -> face, neck, background and face sub-region masks -> mean CIELAB per region.

Landmarks come from OpenCV's YuNet detector: a face box plus five points (both eyes, nose tip,
both mouth corners). Five points are enough because the regions only need to sit on skin and be
large: a large region averages away camera and codec noise (PROJECT.md §9). Nothing here judges
how the face looks.

Geometry lives in a face frame: origin between the eyes, x along the eye line (image left to
image right), y down the face, distances in units of the inter-eye distance (ipd) across and the
eye-to-mouth distance (em) down. Typical adult proportions put the chin about 1.7 em below the
eyes and the hairline about 1 em above them; the face mask stops short of both so it stays on
skin.

Region names are image-based: "face.cheek_l" is the cheek on the image's left. With an unmirrored
camera that is the subject's right cheek, which faces the right half of the subject's screen.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from lumen.colour import bgr8_to_lab

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "face_detection_yunet_2023mar.onnx"
POINT_NAMES = ("eye_r", "eye_l", "nose", "mouth_r", "mouth_l")  # YuNet output order
SUBREGIONS = ("forehead", "nose", "cheek_l", "cheek_r", "chin")
REQUIRED = ("face", "neck")


@dataclass(frozen=True)
class Landmarks:
    box: np.ndarray     # (4,) x, y, w, h in pixels
    points: np.ndarray  # (5, 2) in POINT_NAMES order, pixels
    score: float

    def scaled(self, s: float) -> "Landmarks":
        return replace(self, box=self.box * s, points=self.points * s)


class FaceDetector(Protocol):
    def detect(self, bgr: np.ndarray) -> Landmarks | None: ...


class YuNetDetector:
    """Highest-scoring face from OpenCV's YuNet, on the CPU."""

    def __init__(self, model_path: str | Path = MODEL_PATH, score_threshold: float = 0.7):
        if not Path(model_path).exists():
            raise FileNotFoundError(f"YuNet model not found at {model_path}")
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # silence DNN backend chatter
        self._net = cv2.FaceDetectorYN.create(str(model_path), "", (320, 240), score_threshold)
        self._size: tuple[int, int] | None = None

    def detect(self, bgr: np.ndarray) -> Landmarks | None:
        size = (bgr.shape[1], bgr.shape[0])
        if size != self._size:
            self._net.setInputSize(size)
            self._size = size
        _, faces = self._net.detect(bgr)
        if faces is None or len(faces) == 0:
            return None
        f = faces[np.argmax(faces[:, -1])]
        return Landmarks(box=f[:4].astype(float), points=f[4:14].reshape(5, 2).astype(float),
                         score=float(f[-1]))


@dataclass(frozen=True)
class FaceFrame:
    origin: np.ndarray  # between the eyes
    x: np.ndarray       # unit vector along the eye line, image left to image right
    y: np.ndarray       # unit vector down the face
    ipd: float
    em: float

    def at(self, u: float, v: float) -> np.ndarray:
        """Pixel position of face-frame coordinates (u in ipd, v in em)."""
        return self.origin + u * self.ipd * self.x + v * self.em * self.y

    @property
    def angle_deg(self) -> float:
        return float(np.degrees(np.arctan2(self.x[1], self.x[0])))


def face_frame(lm: Landmarks) -> FaceFrame:
    eye_a, eye_b, _, mouth_a, mouth_b = lm.points
    left, right = sorted((eye_a, eye_b), key=lambda p: p[0])
    x = right - left
    ipd = float(np.linalg.norm(x))
    if ipd < 1:
        raise ValueError("degenerate landmarks: eyes coincide")
    x = x / ipd
    y = np.array([-x[1], x[0]])  # +90 degrees in image coordinates (y points down)
    origin = (eye_a + eye_b) / 2
    em = float(np.dot((mouth_a + mouth_b) / 2 - origin, y))
    if em < 1:
        raise ValueError("degenerate landmarks: mouth not below the eyes")
    return FaceFrame(origin, x, y, ipd, em)


@dataclass(frozen=True)
class Pose:
    yaw: float     # nose-tip offset from the eye midpoint along the eye line, in ipd; ~0 facing
    roll_deg: float
    ipd_px: float  # face size: falls with distance from the camera


def head_pose(lm: Landmarks) -> Pose:
    """Coarse pose from the five points. |yaw| grows roughly 0.1 per 10 degrees of head turn."""
    ff = face_frame(lm)
    return Pose(float(np.dot(lm.points[2] - ff.origin, ff.x) / ff.ipd), ff.angle_deg, ff.ipd)


def _ellipse(mask: np.ndarray, centre, axes, angle_deg: float, value: int) -> None:
    cv2.ellipse(mask, tuple(int(round(c)) for c in centre),
                (max(1, int(round(axes[0]))), max(1, int(round(axes[1])))), angle_deg, 0, 360, value, -1)


def _polygon(mask: np.ndarray, corners, value: int = 255) -> None:
    cv2.fillPoly(mask, [np.round(np.array(corners)).astype(np.int32)], value)


def region_masks(lm: Landmarks, shape: tuple[int, ...], min_pixels: int = 40) -> dict[str, np.ndarray]:
    """uint8 masks (255 inside) for face, neck, background and face.<part> sub-regions.

    Regions that fall mostly outside the frame or come out too small are left out rather than
    silently shrunk. Returns {} when face or neck cannot be placed.
    """
    h, w = shape[:2]
    ff = face_frame(lm)
    angle = ff.angle_deg
    masks: dict[str, np.ndarray] = {}
    expected: dict[str, float] = {}

    # Face skin: ellipse from the forehead to above the chin, minus eyes (with brows) and mouth.
    face = np.zeros((h, w), np.uint8)
    _ellipse(face, ff.at(0, 0.35), (0.85 * ff.ipd, 1.1 * ff.em), angle, 255)
    for u in (-0.5, 0.5):
        _ellipse(face, ff.at(u, -0.12), (0.3 * ff.ipd, 0.24 * ff.em), angle, 0)
    _ellipse(face, ff.at(0, 1.0), (0.45 * ff.ipd, 0.2 * ff.em), angle, 0)
    masks["face"] = face

    # Neck: a band below the chin, clear of the jaw shadow, narrower than the neck itself.
    neck = np.zeros((h, w), np.uint8)
    _polygon(neck, [ff.at(-0.55, 1.95), ff.at(0.55, 1.95), ff.at(0.55, 2.7), ff.at(-0.55, 2.7)])
    masks["neck"] = neck
    expected["neck"] = 1.1 * ff.ipd * 0.75 * ff.em

    # Background: beside the head at eye height, past the ears and hair.
    bg = np.zeros((h, w), np.uint8)
    for u in (-2.4, 2.4):
        du, dv = 0.45, 0.9
        _polygon(bg, [ff.at(u - du, 0.2 - dv), ff.at(u + du, 0.2 - dv), ff.at(u + du, 0.2 + dv),
                      ff.at(u - du, 0.2 + dv)])
    masks["background"] = bg
    expected["background"] = 2 * (0.9 * ff.ipd) * (1.8 * ff.em)

    # Sub-regions: patches that face the screen to different degrees. The cheeks carry L3.
    anchors = {
        "forehead": (ff.at(0, -0.5), 0.25),
        "nose": (lm.points[2], 0.16),
        "cheek_l": (ff.at(-0.6, 0.55), 0.22),
        "cheek_r": (ff.at(0.6, 0.55), 0.22),
        "chin": (ff.at(0, 1.4), 0.2),
    }
    for name, (centre, r) in anchors.items():
        m = np.zeros((h, w), np.uint8)
        radius = max(1, int(round(r * ff.ipd)))
        cv2.circle(m, tuple(int(round(c)) for c in centre), radius, 255, -1)
        masks[f"face.{name}"] = m
        expected[f"face.{name}"] = np.pi * radius**2

    for name in list(masks):
        n = cv2.countNonZero(masks[name])
        if n < min_pixels or (name in expected and n < 0.5 * expected[name]):
            del masks[name]
    if any(r not in masks for r in REQUIRED):
        return {}
    return masks


def region_means(bgr: np.ndarray, masks: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Mean CIELAB (L*, a*, b*) of each masked region.

    The mean is taken in sRGB and then converted: the probe is a small modulation, so averaging
    before the non-linear conversion moves the operating point, not the linear response.
    """
    return {name: bgr8_to_lab(np.array(cv2.mean(bgr, mask=m)[:3])) for name, m in masks.items()}


class RegionExtractor:
    """Frame -> per-region mean Lab, with light landmark smoothing.

    Landmark jitter moves region edges every frame and adds broadband noise to the means; an
    exponential average of the landmarks (alpha) trades a little lag during head motion for a
    lower noise floor. Work is done on a downscaled frame: region means barely change, cost does.
    """

    def __init__(self, detector: FaceDetector | None = None, *, alpha: float = 0.5, work_width: int = 320):
        self.detector = detector if detector is not None else YuNetDetector()
        self.alpha = alpha
        self.work_width = work_width
        self._smoothed: Landmarks | None = None
        self._scale = 1.0
        self.last_pose: Pose | None = None

    def process(self, bgr: np.ndarray) -> dict[str, np.ndarray] | None:
        """Region name -> (L*, a*, b*), or None when no usable face is found."""
        s = min(1.0, self.work_width / bgr.shape[1])
        small = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else bgr
        self._scale = s
        self.last_pose = None
        lm = self.detector.detect(small)
        if lm is None:
            self._smoothed = None
            return None
        if self._smoothed is not None:
            a = self.alpha
            lm = Landmarks(a * lm.box + (1 - a) * self._smoothed.box,
                           a * lm.points + (1 - a) * self._smoothed.points, lm.score)
        self._smoothed = lm
        try:
            masks = region_masks(lm, small.shape)
        except ValueError:
            return None
        if not masks:
            return None
        self.last_pose = head_pose(lm)
        return region_means(small, masks)

    def last_landmarks(self) -> Landmarks | None:
        """Smoothed landmarks of the last processed frame, in that frame's full-size pixels."""
        return None if self._smoothed is None else self._smoothed.scaled(1 / self._scale)
