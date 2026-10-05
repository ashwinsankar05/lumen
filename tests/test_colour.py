import numpy as np

from lumen.colour import bgr8_to_lab, lab_to_linear_srgb, lab_to_srgb, linear_srgb_to_lab, srgb_to_lab


def test_lab_srgb_round_trip():
    rng = np.random.default_rng(0)
    lab = np.column_stack([rng.uniform(30, 90, 500), rng.uniform(-20, 20, 500), rng.uniform(-20, 20, 500)])
    np.testing.assert_allclose(srgb_to_lab(lab_to_srgb(lab)), lab, atol=1e-9)
    np.testing.assert_allclose(linear_srgb_to_lab(lab_to_linear_srgb(lab)), lab, atol=1e-9)


def test_white_and_black():
    np.testing.assert_allclose(srgb_to_lab([1.0, 1.0, 1.0]), [100, 0, 0], atol=1e-3)
    np.testing.assert_allclose(srgb_to_lab([0.0, 0.0, 0.0]), [0, 0, 0], atol=1e-9)


def test_bgr8_order():
    lab = bgr8_to_lab(np.array([0, 0, 255]))  # pure red in OpenCV order
    assert lab[1] > 60  # red is strongly +a*
    np.testing.assert_allclose(lab, srgb_to_lab([1.0, 0.0, 0.0]))


def test_broadcasts_over_leading_axes():
    lab = np.full((4, 2, 3), [70.0, 5.0, -5.0])
    assert lab_to_srgb(lab).shape == (4, 2, 3)
