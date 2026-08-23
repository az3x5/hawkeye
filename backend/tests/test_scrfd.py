"""Tests for the SCRFD detection adapter.

Tests that need the real network are skipped unless weights are present, so
they can never pass silently without a model.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.adapters.base import ModelAdapter
from app.adapters.factory import (
    DetectorNotConfiguredError,
    build_detector,
    build_scrfd_config,
)
from app.adapters.preprocessing import PREPROCESSING_VERSION, UInt8Array
from app.adapters.scrfd import (
    ModelIntegrityError,
    SCRFDConfig,
    SCRFDDetector,
    file_sha256,
)
from app.core.config import Settings
from app.domain.detection import DetectionError

from .conftest import TEST_ENV

WEIGHTS = Path(
    os.environ.get(
        "FACEID_TEST_SCRFD_MODEL_PATH",
        Path(__file__).resolve().parents[2] / "models" / "scrfd_10g_bnkps.onnx",
    )
)

requires_weights = pytest.mark.skipif(
    not WEIGHTS.is_file(),
    reason=f"SCRFD weights not present at {WEIGHTS}; set FACEID_TEST_SCRFD_MODEL_PATH",
)


def _face_image() -> UInt8Array:
    """A real photograph of a real face, shipped offline with scikit-image."""
    skimage_data = pytest.importorskip("skimage.data")
    bgr: UInt8Array = cv2.cvtColor(skimage_data.astronaut(), cv2.COLOR_RGB2BGR)
    return bgr


@pytest.fixture
def detector() -> SCRFDDetector:
    return SCRFDDetector(SCRFDConfig(model_path=WEIGHTS))


class TestConfig:
    @pytest.mark.parametrize("bad", [0.0, -0.5, 1.01])
    def test_invalid_score_threshold_is_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="score_threshold"):
            SCRFDConfig(model_path=WEIGHTS, score_threshold=bad)

    @pytest.mark.parametrize("bad", [0.0, 1.5])
    def test_invalid_nms_threshold_is_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="nms_iou_threshold"):
            SCRFDConfig(model_path=WEIGHTS, nms_iou_threshold=bad)

    def test_input_size_must_suit_the_feature_map_strides(self) -> None:
        with pytest.raises(ValueError, match="multiple of 32"):
            SCRFDConfig(model_path=WEIGHTS, input_size=(500, 640))

    def test_thresholds_are_not_baked_into_the_detector(self) -> None:
        strict = SCRFDConfig(model_path=WEIGHTS, score_threshold=0.9)
        lenient = SCRFDConfig(model_path=WEIGHTS, score_threshold=0.1)
        assert strict.score_threshold != lenient.score_threshold


class TestResourceLimits:
    @pytest.mark.parametrize("field", ["intra_op_threads", "inter_op_threads"])
    def test_thread_counts_below_one_are_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            SCRFDConfig(model_path=WEIGHTS, **{field: 0})

    def test_an_empty_provider_list_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="execution provider"):
            SCRFDConfig(model_path=WEIGHTS, providers=())

    @requires_weights
    def test_an_unavailable_provider_fails_instead_of_falling_back(self) -> None:
        """A GPU that is not there must be an error, never a silent CPU run."""
        detector = SCRFDDetector(
            SCRFDConfig(model_path=WEIGHTS, providers=("NoSuchExecutionProvider",))
        )
        with pytest.raises(ModelIntegrityError, match="NoSuchExecutionProvider"):
            detector.warmup()

    @requires_weights
    def test_bounded_threads_still_detect(self) -> None:
        detector = SCRFDDetector(
            SCRFDConfig(model_path=WEIGHTS, intra_op_threads=1, inter_op_threads=1)
        )
        detector.warmup()
        assert detector.detect(_face_image())


class TestSettingsWiring:
    def test_settings_drive_the_detector_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in TEST_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("FACEID_SCRFD_MODEL_PATH", str(WEIGHTS))
        monkeypatch.setenv("FACEID_SCRFD_SCORE_THRESHOLD", "0.7")
        monkeypatch.setenv("FACEID_SCRFD_NMS_IOU_THRESHOLD", "0.3")
        monkeypatch.setenv("FACEID_SCRFD_INTRA_OP_THREADS", "2")

        config = build_scrfd_config(Settings())  # type: ignore[call-arg]
        assert config.model_path == WEIGHTS
        assert config.score_threshold == pytest.approx(0.7)
        assert config.nms_iou_threshold == pytest.approx(0.3)
        assert config.intra_op_threads == 2
        assert config.providers == ("CPUExecutionProvider",)

    def test_missing_weights_configuration_is_an_explicit_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in TEST_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("FACEID_SCRFD_MODEL_PATH", raising=False)
        with pytest.raises(DetectorNotConfiguredError, match="FACEID_SCRFD_MODEL_PATH"):
            build_detector(Settings())  # type: ignore[call-arg]

    def test_a_malformed_checksum_is_rejected_by_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in TEST_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("FACEID_SCRFD_MODEL_SHA256", "not-a-digest")
        with pytest.raises(ValueError, match="scrfd_model_sha256"):
            Settings()  # type: ignore[call-arg]


class TestIntegrity:
    def test_missing_weights_file_is_reported(self, tmp_path: Path) -> None:
        detector = SCRFDDetector(SCRFDConfig(model_path=tmp_path / "absent.onnx"))
        with pytest.raises(ModelIntegrityError, match="not found"):
            detector.warmup()

    def test_file_sha256_matches_hashlib(self, tmp_path: Path) -> None:
        import hashlib

        target = tmp_path / "blob.bin"
        payload = b"weights" * 1000
        target.write_bytes(payload)
        assert file_sha256(target) == hashlib.sha256(payload).hexdigest()

    @requires_weights
    def test_a_checksum_mismatch_refuses_to_load(self) -> None:
        detector = SCRFDDetector(SCRFDConfig(model_path=WEIGHTS, expected_sha256="0" * 64))
        with pytest.raises(ModelIntegrityError, match="expected"):
            detector.warmup()

    @requires_weights
    def test_loading_without_a_checksum_warns(
        self, caplog: pytest.LogCaptureFixture, detector: SCRFDDetector
    ) -> None:
        with caplog.at_level(logging.WARNING):
            detector.warmup()
        assert any("without an expected checksum" in r.message for r in caplog.records)

    @requires_weights
    def test_the_matching_checksum_loads(self) -> None:
        digest = file_sha256(WEIGHTS)
        detector = SCRFDDetector(SCRFDConfig(model_path=WEIGHTS, expected_sha256=digest))
        detector.warmup()
        assert detector.model_version == digest


class TestProvenance:
    def test_model_version_is_unknown_before_the_weights_are_loaded(
        self, detector: SCRFDDetector
    ) -> None:
        with pytest.raises(DetectionError, match="warmup"):
            _ = detector.model_version

    def test_preprocessing_version_is_reported_without_loading(
        self, detector: SCRFDDetector
    ) -> None:
        assert detector.preprocessing_version == PREPROCESSING_VERSION

    @requires_weights
    def test_the_adapter_satisfies_the_model_adapter_contract(
        self, detector: SCRFDDetector
    ) -> None:
        assert isinstance(detector, ModelAdapter)
        detector.warmup()
        assert detector.model_name
        assert detector.model_version
        assert detector.preprocessing_version

    @requires_weights
    def test_model_version_is_derived_from_the_weight_bytes(self, detector: SCRFDDetector) -> None:
        detector.warmup()
        assert detector.model_version == file_sha256(WEIGHTS)

    @requires_weights
    def test_warmup_is_idempotent(self, detector: SCRFDDetector) -> None:
        detector.warmup()
        version = detector.model_version
        detector.warmup()
        assert detector.model_version == version


class TestInputValidation:
    @pytest.mark.parametrize(
        "image",
        [
            np.zeros((10, 10), dtype=np.uint8),
            np.zeros((10, 10, 4), dtype=np.uint8),
        ],
    )
    def test_non_bgr_images_are_rejected(self, detector: SCRFDDetector, image: np.ndarray) -> None:
        with pytest.raises(DetectionError, match="HxWx3"):
            detector.detect(image)

    def test_float_images_are_rejected(self, detector: SCRFDDetector) -> None:
        with pytest.raises(DetectionError, match="uint8"):
            detector.detect(np.zeros((10, 10, 3), dtype=np.float32))

    def test_empty_images_are_rejected(self, detector: SCRFDDetector) -> None:
        with pytest.raises(DetectionError, match="empty"):
            detector.detect(np.zeros((0, 0, 3), dtype=np.uint8))


@requires_weights
class TestRealInference:
    def test_finds_the_face_in_a_real_photograph(self, detector: SCRFDDetector) -> None:
        faces = detector.detect(_face_image())
        assert len(faces) == 1
        assert faces[0].score > 0.6

    def test_the_box_lies_inside_the_image(self, detector: SCRFDDetector) -> None:
        image = _face_image()
        height, width = image.shape[:2]
        box = detector.detect(image)[0].box
        assert 0 <= box.x1 < box.x2 <= width
        assert 0 <= box.y1 < box.y2 <= height
        assert box.area > 0.005 * width * height

    def test_landmarks_are_anatomically_ordered(self, detector: SCRFDDetector) -> None:
        points = detector.detect(_face_image())[0].landmarks.as_dict()
        left_eye, right_eye = points["left_eye"], points["right_eye"]
        nose, left_mouth = points["nose"], points["left_mouth"]
        assert left_eye[0] < right_eye[0]
        assert nose[1] > (left_eye[1] + right_eye[1]) / 2
        assert left_mouth[1] > nose[1]

    def test_landmarks_fall_inside_the_detected_box(self, detector: SCRFDDetector) -> None:
        face = detector.detect(_face_image())[0]
        for x, y in face.landmarks.points:
            assert face.box.x1 <= x <= face.box.x2
            assert face.box.y1 <= y <= face.box.y2

    def test_detection_is_deterministic(self, detector: SCRFDDetector) -> None:
        image = _face_image()
        first, second = detector.detect(image), detector.detect(image)
        assert first[0].score == pytest.approx(second[0].score)
        np.testing.assert_allclose(first[0].landmarks.points, second[0].landmarks.points)

    def test_a_blank_image_yields_no_faces(self, detector: SCRFDDetector) -> None:
        assert detector.detect(np.zeros((480, 640, 3), dtype=np.uint8)) == []

    def test_raising_the_threshold_suppresses_weaker_detections(self) -> None:
        image = _face_image()
        lenient = SCRFDDetector(SCRFDConfig(model_path=WEIGHTS, score_threshold=0.3))
        strict = SCRFDDetector(SCRFDConfig(model_path=WEIGHTS, score_threshold=0.999))
        assert len(lenient.detect(image)) >= 1
        assert strict.detect(image) == []

    def test_detections_are_ordered_by_confidence(self, detector: SCRFDDetector) -> None:
        # Two faces side by side: both found, most confident first.
        face = _face_image()
        canvas: UInt8Array = np.zeros((512, 1024, 3), dtype=np.uint8)
        canvas[:, :512], canvas[:, 512:] = face, face
        faces = detector.detect(canvas)
        assert len(faces) == 2
        assert faces[0].score >= faces[1].score

    def test_the_same_person_twice_gives_two_independent_detections(
        self, detector: SCRFDDetector
    ) -> None:
        face = _face_image()
        canvas: UInt8Array = np.zeros((512, 1024, 3), dtype=np.uint8)
        canvas[:, :512], canvas[:, 512:] = face, face
        boxes = [f.box for f in detector.detect(canvas)]
        assert len({round(b.x1) for b in boxes}) == 2

    def test_scaling_the_image_does_not_move_the_face(self, detector: SCRFDDetector) -> None:
        image = _face_image()
        doubled = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR).astype(
            np.uint8
        )
        original = detector.detect(image)[0].box
        scaled = detector.detect(doubled)[0].box
        # Coordinates are reported in each image's own pixels; halving the
        # larger one must land back on the original box.
        assert scaled.x1 / 2 == pytest.approx(original.x1, abs=6.0)
        assert scaled.y1 / 2 == pytest.approx(original.y1, abs=6.0)


@requires_weights
class TestAlignment:
    def test_aligned_crop_has_the_recognition_geometry(self, detector: SCRFDDetector) -> None:
        image = _face_image()
        aligned = detector.align(image, detector.detect(image)[0])
        assert aligned.image.shape == (112, 112, 3)
        assert aligned.image.dtype == np.uint8
        assert aligned.preprocessing_version == PREPROCESSING_VERSION

    def test_the_crop_carries_its_detection(self, detector: SCRFDDetector) -> None:
        image = _face_image()
        face = detector.detect(image)[0]
        assert detector.align(image, face).detection is face

    def test_alignment_normalises_an_in_plane_rotation(self, detector: SCRFDDetector) -> None:
        image = _face_image()
        upright = detector.detect_and_align(image)[0].image

        rotation = cv2.getRotationMatrix2D((256.0, 256.0), 20.0, 1.0)
        rotated = cv2.warpAffine(image, rotation, (512, 512)).astype(np.uint8)
        realigned = detector.detect_and_align(rotated)[0].image

        # Crops of the same face at different orientations must land close
        # together, which is the whole point of aligning before recognition.
        difference = np.abs(upright.astype(np.int16) - realigned.astype(np.int16)).mean()
        assert difference < 25.0

    def test_one_aligned_crop_per_detected_face(self, detector: SCRFDDetector) -> None:
        face = _face_image()
        canvas: UInt8Array = np.zeros((512, 1024, 3), dtype=np.uint8)
        canvas[:, :512], canvas[:, 512:] = face, face
        assert len(detector.detect_and_align(canvas)) == 2

    def test_crop_size_is_configurable_for_other_recognition_backbones(
        self, detector: SCRFDDetector
    ) -> None:
        image = _face_image()
        aligned = detector.align(image, detector.detect(image)[0], size=224)
        assert aligned.image.shape == (224, 224, 3)
