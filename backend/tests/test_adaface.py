"""Tests for the AdaFace recognition adapter.

Tests needing the real network are skipped unless the weights are present, so
they can never pass silently without a model.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.adapters.preprocessing import PREPROCESSING_VERSION, UInt8Array
from app.adapters.scrfd import ModelIntegrityError, SCRFDConfig, SCRFDDetector
from app.core.config import Settings
from app.domain.detection import AlignedFace, BoundingBox, DetectedFace, FaceLandmarks
from app.domain.recognition import (
    EmbeddingProvenance,
    FaceEmbedding,
    IncomparableEmbeddingsError,
    RecognitionError,
)

from .conftest import TEST_ENV
from .test_scrfd import WEIGHTS as SCRFD_WEIGHTS

torch = pytest.importorskip("torch")

from app.adapters.adaface import (  # noqa: E402
    EMBEDDING_DIM,
    INPUT_SIZE,
    AdaFaceConfig,
    AdaFaceRecognizer,
    cosine_similarity,
)
from app.adapters.factory import (  # noqa: E402
    RecognizerNotConfiguredError,
    build_adaface_config,
    build_recognizer,
)
from app.adapters.iresnet import Backbone, count_parameters, ir_101  # noqa: E402

WEIGHTS = Path(
    os.environ.get(
        "FACEID_TEST_ADAFACE_MODEL_PATH",
        Path(__file__).resolve().parents[2] / "models" / "adaface_ir101_webface12m.safetensors",
    )
)

requires_weights = pytest.mark.skipif(
    not WEIGHTS.is_file(),
    reason=f"AdaFace weights not present at {WEIGHTS}; set FACEID_TEST_ADAFACE_MODEL_PATH",
)
requires_both_models = pytest.mark.skipif(
    not (WEIGHTS.is_file() and SCRFD_WEIGHTS.is_file()),
    reason="both detection and recognition weights are required",
)


def _face_image() -> UInt8Array:
    skimage_data = pytest.importorskip("skimage.data")
    bgr: UInt8Array = cv2.cvtColor(skimage_data.astronaut(), cv2.COLOR_RGB2BGR)
    return bgr


def _aligned_crop(image: UInt8Array) -> AlignedFace:
    detector = SCRFDDetector(SCRFDConfig(model_path=SCRFD_WEIGHTS))
    return detector.detect_and_align(image)[0]


@pytest.fixture
def recognizer() -> AdaFaceRecognizer:
    return AdaFaceRecognizer(AdaFaceConfig(model_path=WEIGHTS))


@pytest.fixture
def blank_crop() -> UInt8Array:
    crop: UInt8Array = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
    return crop


def _detection() -> DetectedFace:
    return DetectedFace(
        box=BoundingBox(x1=0.0, y1=0.0, x2=10.0, y2=10.0),
        score=0.9,
        landmarks=FaceLandmarks(points=np.zeros((5, 2), dtype=np.float32)),
    )


class TestBackbone:
    def test_ir101_has_the_published_parameter_count(self) -> None:
        # IR-101 with a 512-d head is ~65M parameters; a structural mistake in
        # the re-implementation moves this well outside the window.
        assert 60_000_000 < count_parameters(ir_101()) < 70_000_000

    def test_backbone_maps_a_crop_to_the_embedding_width(self) -> None:
        model = ir_101().eval()
        with torch.inference_mode():
            output = model(torch.zeros(2, 3, INPUT_SIZE, INPUT_SIZE))
        assert output.shape == (2, EMBEDDING_DIM)

    def test_unsupported_depth_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported IResNet depth"):
            Backbone(num_layers=77)


class TestConfig:
    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            AdaFaceConfig(model_path=WEIGHTS, batch_size=0)

    def test_config_carries_no_threshold(self) -> None:
        """Recognition has no notion of a match, so there is nothing to tune."""
        assert not {f for f in AdaFaceConfig.__dataclass_fields__ if "threshold" in f}


class TestSettingsWiring:
    def test_settings_drive_the_recogniser_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in TEST_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("FACEID_ADAFACE_MODEL_PATH", str(WEIGHTS))
        monkeypatch.setenv("FACEID_ADAFACE_BATCH_SIZE", "4")

        config = build_adaface_config(Settings())  # type: ignore[call-arg]
        assert config.model_path == WEIGHTS
        assert config.batch_size == 4

    def test_missing_weights_configuration_is_an_explicit_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in TEST_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("FACEID_ADAFACE_MODEL_PATH", raising=False)
        with pytest.raises(RecognizerNotConfiguredError, match="FACEID_ADAFACE_MODEL_PATH"):
            build_recognizer(Settings())  # type: ignore[call-arg]


class TestIntegrity:
    def test_missing_weights_file_is_reported(self, tmp_path: Path) -> None:
        recognizer = AdaFaceRecognizer(AdaFaceConfig(model_path=tmp_path / "absent.safetensors"))
        with pytest.raises(ModelIntegrityError, match="not found"):
            recognizer.warmup()

    @requires_weights
    def test_a_checksum_mismatch_refuses_to_load(self) -> None:
        recognizer = AdaFaceRecognizer(AdaFaceConfig(model_path=WEIGHTS, expected_sha256="0" * 64))
        with pytest.raises(ModelIntegrityError, match="expected"):
            recognizer.warmup()

    @requires_weights
    def test_loading_without_a_checksum_warns(
        self, caplog: pytest.LogCaptureFixture, recognizer: AdaFaceRecognizer
    ) -> None:
        with caplog.at_level(logging.WARNING):
            recognizer.warmup()
        assert any("without an expected checksum" in r.message for r in caplog.records)

    @requires_weights
    def test_the_published_architecture_loads_strictly(self, recognizer: AdaFaceRecognizer) -> None:
        """A checkpoint that does not fit the architecture must fail, not warn."""
        recognizer.warmup()
        assert recognizer.model_version


class TestProvenance:
    def test_model_version_is_unknown_before_loading(self, recognizer: AdaFaceRecognizer) -> None:
        with pytest.raises(RecognitionError, match="warmup"):
            _ = recognizer.model_version

    def test_preprocessing_version_matches_the_detector(
        self, recognizer: AdaFaceRecognizer
    ) -> None:
        """Recognition inherits the detector's alignment; both must agree."""
        assert recognizer.preprocessing_version == PREPROCESSING_VERSION

    @requires_weights
    def test_the_adapter_satisfies_the_model_adapter_contract(
        self, recognizer: AdaFaceRecognizer
    ) -> None:
        from app.adapters.base import ModelAdapter

        assert isinstance(recognizer, ModelAdapter)
        recognizer.warmup()
        assert recognizer.model_name
        assert recognizer.model_version
        assert recognizer.preprocessing_version

    @requires_weights
    def test_every_embedding_carries_the_full_triple(
        self, recognizer: AdaFaceRecognizer, blank_crop: UInt8Array
    ) -> None:
        provenance = recognizer.embed(blank_crop).provenance
        assert provenance.model_name == "adaface_ir101_webface12m"
        assert provenance.model_version == recognizer.model_version
        assert provenance.preprocessing_version == PREPROCESSING_VERSION

    @requires_weights
    def test_warmup_is_idempotent(self, recognizer: AdaFaceRecognizer) -> None:
        recognizer.warmup()
        version = recognizer.model_version
        recognizer.warmup()
        assert recognizer.model_version == version


class TestInputValidation:
    def test_wrong_crop_size_is_rejected(self, recognizer: AdaFaceRecognizer) -> None:
        with pytest.raises(RecognitionError, match="112x112"):
            recognizer.embed(np.zeros((64, 64, 3), dtype=np.uint8))

    def test_non_bgr_crop_is_rejected(self, recognizer: AdaFaceRecognizer) -> None:
        with pytest.raises(RecognitionError, match="HxWx3"):
            recognizer.embed(np.zeros((INPUT_SIZE, INPUT_SIZE), dtype=np.uint8))

    def test_float_crop_is_rejected(self, recognizer: AdaFaceRecognizer) -> None:
        with pytest.raises(RecognitionError, match="uint8"):
            recognizer.embed(np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.float32))

    def test_a_crop_from_different_preprocessing_is_refused(
        self, recognizer: AdaFaceRecognizer, blank_crop: UInt8Array
    ) -> None:
        stale = AlignedFace(
            detection=_detection(), image=blank_crop, preprocessing_version="something-older-v0"
        )
        with pytest.raises(RecognitionError, match="preprocessing"):
            recognizer.embed(stale)

    def test_an_empty_batch_returns_nothing(self, recognizer: AdaFaceRecognizer) -> None:
        assert recognizer.embed_batch([]) == []


@requires_weights
class TestRealInference:
    def test_embedding_has_the_published_width_and_is_normalised(
        self, recognizer: AdaFaceRecognizer, blank_crop: UInt8Array
    ) -> None:
        embedding = recognizer.embed(blank_crop)
        assert embedding.dimension == EMBEDDING_DIM
        assert np.linalg.norm(embedding.vector) == pytest.approx(1.0, abs=1e-4)

    def test_embedding_is_deterministic(
        self, recognizer: AdaFaceRecognizer, blank_crop: UInt8Array
    ) -> None:
        first, second = recognizer.embed(blank_crop), recognizer.embed(blank_crop)
        np.testing.assert_allclose(first.vector, second.vector, atol=1e-6)

    def test_batching_matches_one_at_a_time(self, recognizer: AdaFaceRecognizer) -> None:
        rng = np.random.default_rng(7)
        crops: list[UInt8Array] = [
            rng.integers(0, 256, size=(INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8) for _ in range(5)
        ]
        batched = AdaFaceRecognizer(AdaFaceConfig(model_path=WEIGHTS, batch_size=2))
        for left, right in zip(
            batched.embed_batch(crops), [recognizer.embed(c) for c in crops], strict=True
        ):
            assert cosine_similarity(left, right) == pytest.approx(1.0, abs=1e-4)

    def test_batch_order_is_preserved(self, recognizer: AdaFaceRecognizer) -> None:
        rng = np.random.default_rng(1)
        crops: list[UInt8Array] = [
            rng.integers(0, 256, size=(INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8) for _ in range(4)
        ]
        batched = recognizer.embed_batch(crops)
        for index, crop in enumerate(crops):
            assert cosine_similarity(batched[index], recognizer.embed(crop)) == pytest.approx(
                1.0, abs=1e-4
            )

    def test_similarity_refuses_across_model_versions(
        self, recognizer: AdaFaceRecognizer, blank_crop: UInt8Array
    ) -> None:
        real = recognizer.embed(blank_crop)
        foreign = FaceEmbedding(
            vector=real.vector.copy(),
            provenance=EmbeddingProvenance(
                model_name=real.provenance.model_name,
                model_version="0" * 64,
                preprocessing_version=real.provenance.preprocessing_version,
            ),
        )
        with pytest.raises(IncomparableEmbeddingsError):
            cosine_similarity(real, foreign)


@requires_both_models
class TestIdentityBehaviour:
    """End-to-end behaviour over the detect -> align -> embed pipeline."""

    def test_a_face_matches_itself(self, recognizer: AdaFaceRecognizer) -> None:
        crop = _aligned_crop(_face_image())
        assert cosine_similarity(recognizer.embed(crop), recognizer.embed(crop)) > 0.99

    def test_identity_survives_an_in_plane_rotation(self, recognizer: AdaFaceRecognizer) -> None:
        image = _face_image()
        upright = recognizer.embed(_aligned_crop(image))
        rotation = cv2.getRotationMatrix2D((256.0, 256.0), 15.0, 1.0)
        rotated = cv2.warpAffine(image, rotation, (512, 512)).astype(np.uint8)
        similarity = cosine_similarity(upright, recognizer.embed(_aligned_crop(rotated)))
        assert similarity > 0.8, f"same face after rotation scored only {similarity:.3f}"

    def test_identity_survives_a_brightness_change(self, recognizer: AdaFaceRecognizer) -> None:
        image = _face_image()
        original = recognizer.embed(_aligned_crop(image))
        brightened = cv2.convertScaleAbs(image, alpha=0.7).astype(np.uint8)
        darker = recognizer.embed(_aligned_crop(brightened))
        assert cosine_similarity(original, darker) > 0.8

    def test_a_face_scores_far_higher_against_itself_than_against_noise(
        self, recognizer: AdaFaceRecognizer
    ) -> None:
        rng = np.random.default_rng(0)
        crop = _aligned_crop(_face_image())
        noise: UInt8Array = rng.integers(0, 256, size=(INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
        genuine = cosine_similarity(recognizer.embed(crop), recognizer.embed(crop))
        impostor = cosine_similarity(recognizer.embed(crop), recognizer.embed(noise))
        assert genuine - impostor > 0.5

    def test_many_samples_of_one_person_each_get_their_own_embedding(
        self, recognizer: AdaFaceRecognizer
    ) -> None:
        """A person has many face samples; none is treated as canonical."""
        image = _face_image()
        variants = [
            image,
            cv2.convertScaleAbs(image, alpha=0.8).astype(np.uint8),
            cv2.warpAffine(
                image, cv2.getRotationMatrix2D((256.0, 256.0), 8.0, 1.0), (512, 512)
            ).astype(np.uint8),
        ]
        embeddings = recognizer.embed_batch([_aligned_crop(v) for v in variants])
        assert len(embeddings) == 3
        for other in embeddings[1:]:
            assert cosine_similarity(embeddings[0], other) > 0.6

    def test_recognition_returns_scores_not_verdicts(self, recognizer: AdaFaceRecognizer) -> None:
        """The adapter exposes no decision surface at all."""
        crop = _aligned_crop(_face_image())
        result = cosine_similarity(recognizer.embed(crop), recognizer.embed(crop))
        assert isinstance(result, float)
        assert not hasattr(recognizer, "is_match")
        assert not hasattr(recognizer, "verify")
        assert not hasattr(recognizer, "identify")
