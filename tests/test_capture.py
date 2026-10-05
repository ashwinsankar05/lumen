import time

import cv2
import numpy as np
import pytest

from lumen.capture import CaptureReport, FrameGrabber, VideoFileSource, open_source


class ListSource:
    """Yields prepared items; None means a failed read; runs out with EOFError."""

    def __init__(self, items, delay=0.0):
        self.items = list(items)
        self.delay = delay
        self.report = CaptureReport(source="list")
        self.locks = 0

    def read(self):
        time.sleep(self.delay)
        if not self.items:
            raise EOFError
        return self.items.pop(0)

    def lock(self):
        self.locks += 1

    def close(self):
        pass


def frames(n):
    return [np.full((4, 4, 3), i % 256, np.uint8) for i in range(n)]


def wait_for(cond, timeout=3.0):
    end = time.perf_counter() + timeout
    while not cond() and time.perf_counter() < end:
        time.sleep(0.005)
    return cond()


def test_grabber_timestamps_every_frame_in_order_and_ends():
    ticks = iter(np.arange(1000) * 0.033 + 100.0)
    g = FrameGrabber(ListSource(frames(5)), now=lambda: next(ticks)).start()
    assert wait_for(lambda: g.ended)
    got = [g.frames.get_nowait() for _ in range(5)]
    assert [f.index for f in got] == list(range(5))
    assert np.all(np.diff([f.t for f in got]) > 0)
    assert g.dropped == 0 and g.error is None


def test_slow_consumer_drops_oldest_and_counts_them():
    g = FrameGrabber(ListSource(frames(20)), queue_size=4).start()
    assert wait_for(lambda: g.ended)
    kept = [g.frames.get_nowait().index for _ in range(g.frames.qsize())]
    assert kept == [16, 17, 18, 19]
    assert g.dropped == 16 and g.count == 20


def test_repeated_failed_reads_become_a_capture_error():
    g = FrameGrabber(ListSource(frames(2) + [None] * 10), max_failures=5).start()
    assert wait_for(lambda: not g.running)
    assert g.error is not None and "5 consecutive" in g.error
    assert g.count == 2


def test_lock_runs_on_the_grabber_thread():
    src = ListSource(frames(50), delay=0.002)
    g = FrameGrabber(src).start()
    g.request_lock()
    assert wait_for(lambda: g.locked_at is not None)
    g.stop()
    assert src.locks == 1


def test_producer_timestamps_win_over_arrival_time():
    src = ListSource(frames(3))
    src.last_frame_time = 42.0
    g = FrameGrabber(src).start()
    assert wait_for(lambda: g.ended)
    assert g.frames.get_nowait().t == 42.0


def test_measured_fps():
    ticks = iter(np.arange(1000) / 30.0)
    g = FrameGrabber(ListSource(frames(60)), queue_size=100, now=lambda: next(ticks)).start()
    assert wait_for(lambda: g.ended)
    assert g.fps() == pytest.approx(30.0)


def test_video_file_source_plays_in_real_time(tmp_path):
    path = tmp_path / "clip.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (32, 24))
    for f in range(10):
        w.write(np.full((24, 32, 3), f * 20, np.uint8))
    w.release()

    src = open_source(f"file:{path}")
    assert isinstance(src, VideoFileSource) and src.fps == pytest.approx(20.0)
    start = time.perf_counter()
    n = 0
    with pytest.raises(EOFError):
        while True:
            assert src.read().shape == (24, 32, 3)
            n += 1
    assert n == 10
    assert time.perf_counter() - start >= 9 / 20 - 0.02
    src.close()


def test_exposure_search_never_exceeds_one_frame_period():
    from lumen.capture import exposure_steps_for

    assert exposure_steps_for(30.0) == -5  # 31 ms fits in 33 ms; -4 (62 ms) would halve the rate
    assert exposure_steps_for(15.0) == -4


def test_auto_exposure_is_restored_from_a_short_exposure():
    """Regression: the dev laptop's driver kept a 125 ms trial exposure even back on auto (10 fps)."""
    import cv2

    from lumen.capture import CameraSource

    class FakeCap:
        def __init__(self):
            self.calls = []

        def set(self, prop, value):
            self.calls.append((prop, value))

        def release(self):
            pass

    cam = object.__new__(CameraSource)
    cam.cap, cam.exposures = FakeCap(), list(range(-9, -4))
    cam._restore_exposure, cam._restore_wb = True, False
    cam.close()
    assert cam.cap.calls == [(cv2.CAP_PROP_AUTO_EXPOSURE, CameraSource.AUTO_OFF),
                             (cv2.CAP_PROP_EXPOSURE, -5),
                             (cv2.CAP_PROP_AUTO_EXPOSURE, CameraSource.AUTO_ON)]


def test_unknown_source_is_refused():
    with pytest.raises(ValueError, match="unknown source"):
        open_source("webcam:0")
