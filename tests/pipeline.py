"""Offline pipeline helper for tests: synthetic scene -> regions -> features, no threads or clock.

SIMULATED data only.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from lumen.features import Features, analyse
from lumen.probe import EmissionLog, Probe
from lumen.regions import RegionExtractor
from lumen.synthetic import FixedDetector, SyntheticScene


@lru_cache(maxsize=None)
def offline(scenario: str, *, layout: str = "split", duration: float = 30.0, seed: int = 0,
            amplitude: float = 24.0) -> Features:
    probe = Probe(f"offline-{scenario}-{seed}", layout=layout, duration_s=duration, amplitude=amplitude)
    t0 = 10.0
    log = EmissionLog(probe, t0)
    for t in t0 + np.arange(int(duration * 60)) / 60:
        log.emit(t)
    scene = SyntheticScene(scenario, lambda: (probe, t0), seed=seed)
    ex = RegionExtractor(FixedDetector())
    ts = t0 + 0.4 + np.arange(int((duration - 1) * 30)) / 30
    samples = [ex.process(scene.render(t)) for t in ts]
    names = samples[0].keys()
    regions = {n: (ts, np.array([s[n][1:] for s in samples])) for n in names}
    lightness = float(np.mean([s["face"][0] for s in samples]) / np.mean([s["neck"][0] for s in samples]))
    return analyse(log.t, log.chroma, regions, face_neck_lightness=lightness)
