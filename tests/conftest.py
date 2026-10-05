import numpy as np
import pytest


class FakeDisplay:
    """Stands in for the OpenCV window: records frames, optionally presses a key."""

    def __init__(self, press_at: int | None = None, key: int = 27):
        self.frames: list[np.ndarray] = []
        self.press_at = press_at
        self.key = key
        self.closed = False

    def show(self, bgr: np.ndarray) -> int:
        self.frames.append(bgr)
        return self.key if self.press_at is not None and len(self.frames) >= self.press_at else -1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_display():
    return FakeDisplay
