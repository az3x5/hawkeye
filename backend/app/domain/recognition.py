"""Value objects produced by face recognition.

A recognition model measures faces; it does not decide who anyone is. These
types therefore carry vectors and provenance, and nothing resembling a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


class RecognitionError(Exception):
    """An embedding could not be produced from the given input."""


class IncomparableEmbeddingsError(Exception):
    """Two embeddings were produced under different conditions.

    Vectors are only comparable when the model and the preprocessing that
    produced them agree. Comparing across a model or preprocessing change
    silently degrades accuracy in a way no downstream check would notice.
    """


@dataclass(frozen=True, slots=True)
class EmbeddingProvenance:
    """Exactly what produced an embedding.

    Recorded with every vector so a model or preprocessing upgrade is
    detectable rather than quietly poisoning the vector space.
    """

    model_name: str
    model_version: str
    preprocessing_version: str

    def __post_init__(self) -> None:
        """Reject incomplete provenance."""
        for field_name in ("model_name", "model_version", "preprocessing_version"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")

    def matches(self, other: EmbeddingProvenance) -> bool:
        """True when vectors from both provenances may be compared."""
        return self == other


@dataclass(frozen=True, slots=True)
class FaceEmbedding:
    """One L2-normalised face embedding and the provenance that produced it."""

    vector: npt.NDArray[np.float32]
    provenance: EmbeddingProvenance

    def __post_init__(self) -> None:
        """Validate the vector's shape, dtype and normalisation."""
        if self.vector.ndim != 1:
            raise ValueError(f"embedding must be a 1-D vector, got shape {self.vector.shape}")
        if self.vector.dtype != np.float32:
            raise ValueError(f"embedding must be float32, got {self.vector.dtype}")
        if not np.isfinite(self.vector).all():
            raise ValueError("embedding contains non-finite values")
        norm = float(np.linalg.norm(self.vector))
        if not np.isclose(norm, 1.0, atol=1e-3):
            raise ValueError(f"embedding must be L2-normalised, got norm {norm:.6f}")

    @property
    def dimension(self) -> int:
        """Number of components in the vector."""
        return int(self.vector.shape[0])
