import numpy as np
import pytest

from lumen.colour import bgr8_to_lab, lab_to_srgb
from lumen.regions import (
    SUBREGIONS,
    Landmarks,
    RegionExtractor,
    YuNetDetector,
    face_frame,
    head_pose,
    region_masks,
    region_means,
)
from lumen.synthetic import LANDMARKS, SIZE, FixedDetector


def centroid(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    return np.array([xs.mean(), ys.mean()])


def test_all_regions_placed_on_a_centred_face():
    masks = region_masks(LANDMARKS, SIZE)
    assert {"face", "neck", "background"} <= set(masks)
    assert {f"face.{s}" for s in SUBREGIONS} <= set(masks)


def test_regions_sit_where_anatomy_says():
    masks = region_masks(LANDMARKS, SIZE)
    eyes_y = 70
    assert centroid(masks["neck"])[1] > centroid(masks["face"])[1] > eyes_y - 20
    assert centroid(masks["face.forehead"])[1] < eyes_y < centroid(masks["face.chin"])[1]
    assert centroid(masks["face.cheek_l"])[0] < 160 < centroid(masks["face.cheek_r"])[0]
    # face and neck do not overlap; background is outside the face
    assert not np.any((masks["face"] > 0) & (masks["neck"] > 0))
    assert not np.any((masks["face"] > 0) & (masks["background"] > 0))


def test_face_mask_excludes_eyes_and_mouth():
    face = region_masks(LANDMARKS, SIZE)["face"]
    for x, y in LANDMARKS.points[[0, 1]]:
        assert face[int(y), int(x)] == 0
    mouth = LANDMARKS.points[3:].mean(axis=0)
    assert face[int(mouth[1]), int(mouth[0])] == 0


def test_masks_follow_head_roll():
    theta = np.radians(20)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    c = np.array([160.0, 90.0])
    rolled = Landmarks(LANDMARKS.box, (LANDMARKS.points - c) @ rot.T + c, 0.9)
    upright = centroid(region_masks(LANDMARKS, SIZE)["neck"]) - c
    tilted = centroid(region_masks(rolled, SIZE)["neck"]) - c
    np.testing.assert_allclose(tilted, rot @ upright, atol=1.5)


def test_face_near_the_bottom_edge_loses_its_neck_and_everything():
    low = LANDMARKS.scaled(1.0)
    low = Landmarks(low.box, low.points + [0, 120], low.score)
    assert region_masks(low, SIZE) == {}


def test_degenerate_landmarks_are_refused():
    flat = Landmarks(LANDMARKS.box, np.tile([[150.0, 70.0]], (5, 1)), 0.9)
    with pytest.raises(ValueError):
        face_frame(flat)


def test_head_pose_yaw_sign_and_size():
    straight = head_pose(LANDMARKS)
    assert straight.yaw == pytest.approx(0.0, abs=1e-9)
    assert straight.ipd_px == pytest.approx(40.0)
    turned = Landmarks(LANDMARKS.box, LANDMARKS.points + np.array([[0, 0], [0, 0], [6, 0], [0, 0], [0, 0]]), 0.9)
    assert head_pose(turned).yaw == pytest.approx(6 / 40)


def test_region_means_recover_painted_colours():
    masks = region_masks(LANDMARKS, SIZE)
    img = np.zeros(SIZE + (3,), np.uint8)
    face_lab, neck_lab = np.array([65.0, 15.0, 18.0]), np.array([55.0, 10.0, 20.0])
    img[masks["face"] > 0] = np.round(lab_to_srgb(face_lab)[::-1] * 255)
    img[masks["neck"] > 0] = np.round(lab_to_srgb(neck_lab)[::-1] * 255)
    means = region_means(img, {k: masks[k] for k in ("face", "neck")})
    np.testing.assert_allclose(means["face"], face_lab, atol=0.5)
    np.testing.assert_allclose(means["neck"], neck_lab, atol=0.5)
    np.testing.assert_allclose(bgr8_to_lab(img[masks["face"] > 0][0]), means["face"])


def test_extractor_works_on_a_downscaled_frame_and_reports_pose():
    ex = RegionExtractor(FixedDetector(), work_width=160)
    big = np.full((480, 640, 3), 128, np.uint8)
    out = ex.process(big)
    assert out is not None and {"face", "neck"} <= set(out)
    assert ex.last_pose is not None
    np.testing.assert_allclose(ex.last_landmarks().points, LANDMARKS.points * 2, atol=1e-6)


def test_extractor_returns_none_without_a_face():
    class NoFace:
        def detect(self, bgr):
            return None

    ex = RegionExtractor(NoFace())
    assert ex.process(np.zeros((240, 320, 3), np.uint8)) is None
    assert ex.last_pose is None


def test_yunet_loads_and_finds_no_face_in_a_blank_frame():
    assert YuNetDetector().detect(np.zeros((240, 320, 3), np.uint8)) is None
