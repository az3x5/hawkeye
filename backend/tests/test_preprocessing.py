"""Unit tests for the pure image and tensor operations behind detection.

These need no model: they pin down the coordinate handling and geometry that
is easiest to get subtly — and silently — wrong.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.adapters.preprocessing import (
    ARCFACE_TEMPLATE_112,
    UInt8Array,
    align_face,
    anchor_centers,
    distance2bbox,
    distance2kps,
    letterbox,
    nms,
    to_blob,
    umeyama_similarity,
)


def _noise_image(height: int, width: int, seed: int = 0) -> UInt8Array:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


class TestLetterbox:
    def test_output_matches_the_requested_canvas(self) -> None:
        canvas, _ = letterbox(_noise_image(480, 640), (640, 640))
        assert canvas.shape == (640, 640, 3)

    def test_aspect_ratio_is_preserved(self) -> None:
        canvas, scale = letterbox(_noise_image(400, 800), (640, 640))
        assert scale == pytest.approx(0.8)
        # 800x400 at 0.8 fills the full width and half the height.
        assert canvas[:320, :640].any()
        assert not canvas[320:, :].any()

    def test_padding_is_bottom_right_so_mapping_back_is_a_pure_division(self) -> None:
        image = np.full((100, 50, 3), 255, dtype=np.uint8)
        canvas, scale = letterbox(image, (640, 640))
        # The content starts at the origin: no offset to subtract later.
        assert canvas[0, 0].any()
        assert not canvas[-1, -1].any()
        assert scale == pytest.approx(6.4)

    def test_a_square_image_uses_the_whole_canvas(self) -> None:
        canvas, scale = letterbox(_noise_image(320, 320), (640, 640))
        assert scale == pytest.approx(2.0)
        assert canvas.all() or canvas.any()

    def test_empty_image_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty image"):
            letterbox(np.zeros((0, 0, 3), dtype=np.uint8), (640, 640))


class TestToBlob:
    def test_shape_is_nchw(self) -> None:
        blob = to_blob(_noise_image(640, 640))
        assert blob.shape == (1, 3, 640, 640)
        assert blob.dtype == np.float32

    def test_normalisation_maps_mid_grey_to_zero(self) -> None:
        image = np.full((64, 64, 3), 128, dtype=np.uint8)
        blob = to_blob(image)
        assert blob.min() == pytest.approx((128 - 127.5) / 128.0, abs=1e-6)

    def test_channels_are_swapped_to_rgb(self) -> None:
        image = np.zeros((32, 32, 3), dtype=np.uint8)
        image[:, :, 0] = 255  # blue in BGR
        blob = to_blob(image)
        # After swapRB the blue channel must land last.
        assert blob[0, 2].mean() > blob[0, 0].mean()


class TestAnchorCenters:
    def test_centers_are_spaced_by_the_stride(self) -> None:
        centers = anchor_centers(2, 2, stride=8, num_anchors=1)
        assert centers.tolist() == [[0, 0], [8, 0], [0, 8], [8, 8]]

    def test_anchors_per_cell_are_consecutive(self) -> None:
        centers = anchor_centers(1, 2, stride=8, num_anchors=2)
        assert centers.tolist() == [[0, 0], [0, 0], [8, 0], [8, 0]]

    def test_count_matches_the_flattened_model_output(self) -> None:
        # 640/8 = 80 cells per side, two anchors each: the 12800 rows SCRFD emits.
        assert anchor_centers(80, 80, stride=8, num_anchors=2).shape == (12800, 2)


class TestDecoding:
    def test_distance2bbox_expands_around_the_anchor(self) -> None:
        centers = np.array([[10.0, 10.0]], dtype=np.float32)
        distances = np.array([[1.0, 2.0, 3.0, 4.0]], dtype=np.float32)
        assert distance2bbox(centers, distances).tolist() == [[9.0, 8.0, 13.0, 14.0]]

    def test_distance2kps_offsets_each_keypoint(self) -> None:
        centers = np.array([[10.0, 20.0]], dtype=np.float32)
        distances = np.arange(10, dtype=np.float32).reshape(1, 10)
        points = distance2kps(centers, distances)
        assert points.shape == (1, 5, 2)
        assert points[0, 0].tolist() == [10.0, 21.0]
        assert points[0, 4].tolist() == [18.0, 29.0]


class TestNms:
    def test_keeps_the_highest_scoring_of_overlapping_boxes(self) -> None:
        boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11]], dtype=np.float32)
        scores = np.array([0.6, 0.9], dtype=np.float32)
        assert nms(boxes, scores, 0.4) == [1]

    def test_keeps_disjoint_boxes(self) -> None:
        boxes = np.array([[0, 0, 10, 10], [100, 100, 110, 110]], dtype=np.float32)
        scores = np.array([0.6, 0.9], dtype=np.float32)
        assert sorted(nms(boxes, scores, 0.4)) == [0, 1]

    def test_returns_indices_best_score_first(self) -> None:
        boxes = np.array([[0, 0, 10, 10], [50, 50, 60, 60], [100, 100, 110, 110]], dtype=np.float32)
        scores = np.array([0.3, 0.9, 0.6], dtype=np.float32)
        assert nms(boxes, scores, 0.4) == [1, 2, 0]

    def test_a_looser_threshold_keeps_more_boxes(self) -> None:
        boxes = np.array([[0, 0, 10, 10], [5, 0, 15, 10]], dtype=np.float32)
        scores = np.array([0.9, 0.8], dtype=np.float32)
        assert nms(boxes, scores, 0.1) == [0]
        assert sorted(nms(boxes, scores, 0.9)) == [0, 1]

    def test_empty_input_gives_empty_output(self) -> None:
        assert nms(np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), 0.4) == []

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
    def test_invalid_threshold_is_rejected(self, bad: float) -> None:
        boxes = np.array([[0, 0, 10, 10]], dtype=np.float32)
        with pytest.raises(ValueError, match="iou_threshold"):
            nms(boxes, np.array([0.9], np.float32), bad)


class TestUmeyamaSimilarity:
    def test_recovers_a_known_rotation_scale_and_translation(self) -> None:
        source = np.array([[0, 0], [1, 0], [0, 1], [2, 2], [3, 1]], dtype=np.float32)
        angle, scale, shift = np.pi / 6, 2.5, np.array([7.0, -3.0])
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        target = (scale * source @ rotation.T + shift).astype(np.float32)

        matrix = umeyama_similarity(source, target)
        mapped = source @ matrix[:, :2].T + matrix[:, 2]
        np.testing.assert_allclose(mapped, target, atol=1e-4)

    def test_is_deterministic(self) -> None:
        source = np.array([[0, 0], [1, 0], [0, 1], [2, 2], [3, 1]], dtype=np.float32)
        target = source * 1.7 + 4.0
        first = umeyama_similarity(source, target)
        for _ in range(5):
            np.testing.assert_array_equal(umeyama_similarity(source, target), first)

    def test_mismatched_point_sets_are_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\(N, 2\)"):
            umeyama_similarity(np.zeros((5, 2), np.float32), np.zeros((4, 2), np.float32))

    def test_degenerate_source_is_rejected(self) -> None:
        identical = np.ones((5, 2), dtype=np.float32)
        with pytest.raises(ValueError, match="degenerate"):
            umeyama_similarity(identical, ARCFACE_TEMPLATE_112)


class TestAlignFace:
    def test_landmarks_land_on_the_canonical_template(self) -> None:
        image = _noise_image(300, 300, seed=1)
        # A rotated, scaled, shifted version of the template.
        angle = np.deg2rad(12.0)
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        landmarks = (ARCFACE_TEMPLATE_112 * 1.4 @ rotation.T + [60.0, 40.0]).astype(np.float32)

        matrix = umeyama_similarity(landmarks, ARCFACE_TEMPLATE_112)
        mapped = landmarks @ matrix[:, :2].T + matrix[:, 2]
        np.testing.assert_allclose(mapped, ARCFACE_TEMPLATE_112, atol=1e-3)

        crop = align_face(image, landmarks)
        assert crop.shape == (112, 112, 3)
        assert crop.dtype == np.uint8

    def test_crop_size_scales_the_template(self) -> None:
        image = _noise_image(300, 300, seed=2)
        landmarks = (ARCFACE_TEMPLATE_112 + 50.0).astype(np.float32)
        assert align_face(image, landmarks, size=224).shape == (224, 224, 3)

    def test_alignment_undoes_an_in_plane_rotation(self) -> None:
        # A distinctive patch, rotated: alignment should bring it back.
        image: UInt8Array = np.zeros((300, 300, 3), dtype=np.uint8)
        cv2.rectangle(image, (100, 100), (160, 140), (255, 255, 255), -1)
        upright = (ARCFACE_TEMPLATE_112 + [100.0, 100.0]).astype(np.float32)
        straight = align_face(image, upright)

        angle = 25.0
        rotation_matrix = cv2.getRotationMatrix2D((150.0, 150.0), angle, 1.0)
        rotated_image = cv2.warpAffine(image, rotation_matrix, (300, 300)).astype(np.uint8)
        rotated_landmarks = (upright @ rotation_matrix[:, :2].T + rotation_matrix[:, 2]).astype(
            np.float32
        )
        realigned = align_face(rotated_image, rotated_landmarks)

        overlap = np.mean((straight > 127) == (realigned > 127))
        assert overlap > 0.95
