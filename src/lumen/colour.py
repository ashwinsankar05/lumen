"""sRGB <-> CIELAB (D65), both directions, NumPy only.

The probe is specified in CIELAB and rendered as sRGB; the camera returns sRGB-encoded frames
whose region means are converted back to CIELAB (PROJECT.md §8). All functions broadcast over
leading axes: the last axis holds the three channels.
"""

from __future__ import annotations

import numpy as np

D65_WHITE_XYZ = np.array([0.95047, 1.0, 1.08883])
XYZ_TO_LINEAR_SRGB = np.array([
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
])
LINEAR_SRGB_TO_XYZ = np.linalg.inv(XYZ_TO_LINEAR_SRGB)

_DELTA = 6.0 / 29.0


def lab_to_linear_srgb(lab) -> np.ndarray:
    """CIELAB (D65) -> linear-light sRGB. No clipping: out-of-gamut values stay visible."""
    lab = np.asarray(lab, dtype=float)
    fy = (lab[..., 0] + 16.0) / 116.0
    f = np.stack([fy + lab[..., 1] / 500.0, fy, fy - lab[..., 2] / 200.0], axis=-1)
    xyz = np.where(f > _DELTA, f**3, 3 * _DELTA**2 * (f - 4.0 / 29.0)) * D65_WHITE_XYZ
    return xyz @ XYZ_TO_LINEAR_SRGB.T


def linear_srgb_to_lab(rgb) -> np.ndarray:
    """Linear-light sRGB -> CIELAB (D65)."""
    xyz = np.asarray(rgb, dtype=float) @ LINEAR_SRGB_TO_XYZ.T / D65_WHITE_XYZ
    f = np.where(xyz > _DELTA**3, np.cbrt(xyz), xyz / (3 * _DELTA**2) + 4.0 / 29.0)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
                    axis=-1)


def linear_to_srgb(c) -> np.ndarray:
    """Linear-light sRGB -> gamma-encoded sRGB in [0, 1]."""
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(np.clip(c, 0, None), 1 / 2.4) - 0.055)


def srgb_to_linear(c) -> np.ndarray:
    """Gamma-encoded sRGB in [0, 1] -> linear light."""
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, np.power((np.clip(c, 0, None) + 0.055) / 1.055, 2.4))


def lab_to_srgb(lab) -> np.ndarray:
    return linear_to_srgb(lab_to_linear_srgb(lab))


def srgb_to_lab(rgb) -> np.ndarray:
    return linear_srgb_to_lab(srgb_to_linear(rgb))


def bgr8_to_lab(bgr) -> np.ndarray:
    """OpenCV-order 8-bit BGR values (any float, e.g. a region mean) -> CIELAB."""
    bgr = np.asarray(bgr, dtype=float)
    return srgb_to_lab(bgr[..., ::-1] / 255.0)
