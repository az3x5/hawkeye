"""Invariants of the recognition value objects. No model, no I/O."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from app.domain.recognition import (
    EmbeddingProvenance,
    FaceEmbedding,
    IncomparableEmbeddingsError,
    RecognitionError,
)


def _provenance(**overrides: str) -> EmbeddingProvenance:
    values = {
        "model_name": "adaface_ir101_webface12m",
        "model_version": "a" * 64,
        "preprocessing_version": "scrfd-letterbox-arcface112-v1",
    }
    values.update(overrides)
    return EmbeddingProvenance(**values)  # type: ignore[arg-type]


def _unit_vector(dimension: int = 512, seed: int = 0) -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=dimension).astype(np.float32)
    normalised: npt.NDArray[np.float32] = (vector / np.linalg.norm(vector)).astype(np.float32)
    return normalised


class TestProvenance:
    def test_records_the_full_triple(self) -> None:
        provenance = _provenance()
        assert provenance.model_name
        assert provenance.model_version
        assert provenance.preprocessing_version

    @pytest.mark.parametrize("field", ["model_name", "model_version", "preprocessing_version"])
    def test_incomplete_provenance_is_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            _provenance(**{field: "  "})

    def test_identical_provenance_is_comparable(self) -> None:
        assert _provenance().matches(_provenance())

    @pytest.mark.parametrize("field", ["model_name", "model_version", "preprocessing_version"])
    def test_any_difference_makes_vectors_incomparable(self, field: str) -> None:
        assert not _provenance().matches(_provenance(**{field: "different"}))


class TestFaceEmbedding:
    def test_accepts_a_normalised_vector(self) -> None:
        embedding = FaceEmbedding(vector=_unit_vector(), provenance=_provenance())
        assert embedding.dimension == 512

    def test_rejects_an_unnormalised_vector(self) -> None:
        with pytest.raises(ValueError, match="L2-normalised"):
            FaceEmbedding(vector=_unit_vector() * 3.0, provenance=_provenance())

    def test_rejects_a_multidimensional_array(self) -> None:
        with pytest.raises(ValueError, match="1-D"):
            FaceEmbedding(vector=np.eye(2, dtype=np.float32), provenance=_provenance())

    def test_rejects_the_wrong_dtype(self) -> None:
        with pytest.raises(ValueError, match="float32"):
            FaceEmbedding(vector=_unit_vector().astype(np.float64), provenance=_provenance())

    def test_rejects_non_finite_values(self) -> None:
        vector = _unit_vector().copy()
        vector[0] = np.nan
        with pytest.raises(ValueError, match="non-finite"):
            FaceEmbedding(vector=vector, provenance=_provenance())


class TestSimilarityGuards:
    def test_comparing_across_provenance_is_refused(self) -> None:
        from app.adapters.adaface import cosine_similarity

        first = FaceEmbedding(vector=_unit_vector(seed=1), provenance=_provenance())
        second = FaceEmbedding(
            vector=_unit_vector(seed=2),
            provenance=_provenance(model_version="b" * 64),
        )
        with pytest.raises(IncomparableEmbeddingsError):
            cosine_similarity(first, second)

    def test_identical_vectors_score_one(self) -> None:
        from app.adapters.adaface import cosine_similarity

        vector = _unit_vector(seed=3)
        first = FaceEmbedding(vector=vector, provenance=_provenance())
        second = FaceEmbedding(vector=vector.copy(), provenance=_provenance())
        assert cosine_similarity(first, second) == pytest.approx(1.0, abs=1e-5)

    def test_opposite_vectors_score_minus_one(self) -> None:
        from app.adapters.adaface import cosine_similarity

        vector = _unit_vector(seed=4)
        first = FaceEmbedding(vector=vector, provenance=_provenance())
        second = FaceEmbedding(vector=(-vector).astype(np.float32), provenance=_provenance())
        assert cosine_similarity(first, second) == pytest.approx(-1.0, abs=1e-5)

    def test_similarity_stays_within_range(self) -> None:
        from app.adapters.adaface import cosine_similarity

        for seed in range(10):
            first = FaceEmbedding(vector=_unit_vector(seed=seed), provenance=_provenance())
            second = FaceEmbedding(vector=_unit_vector(seed=seed + 100), provenance=_provenance())
            assert -1.0 <= cosine_similarity(first, second) <= 1.0

    def test_similarity_is_not_a_probability(self) -> None:
        """Scores may be negative, so nothing here can be read as a probability."""
        from app.adapters.adaface import cosine_similarity

        vector = _unit_vector(seed=5)
        first = FaceEmbedding(vector=vector, provenance=_provenance())
        second = FaceEmbedding(vector=(-vector).astype(np.float32), provenance=_provenance())
        assert cosine_similarity(first, second) < 0.0


def test_recognition_error_is_available_for_adapters() -> None:
    assert issubclass(RecognitionError, Exception)
