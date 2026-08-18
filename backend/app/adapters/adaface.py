"""AdaFace face recognition.

The adapter turns aligned face crops into embeddings and reports similarity
between them. It does not decide whether two faces are the same person: it has
no threshold, returns no verdict, and never converts a similarity into a
probability. Those are identity decisions, and they live elsewhere.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file

from app.adapters.iresnet import Backbone, ir_101
from app.adapters.preprocessing import PREPROCESSING_VERSION, UInt8Array
from app.adapters.scrfd import ModelIntegrityError, file_sha256
from app.domain.detection import AlignedFace
from app.domain.recognition import (
    EmbeddingProvenance,
    FaceEmbedding,
    IncomparableEmbeddingsError,
    RecognitionError,
)

logger = logging.getLogger(__name__)

#: Spatial size of the aligned crop the published models were trained on.
INPUT_SIZE = 112

#: Width of the embedding IR-101 produces.
EMBEDDING_DIM = 512

#: Prefix the published checkpoint uses for backbone parameters.
_CHECKPOINT_PREFIX = "model.net."


@dataclass(frozen=True, slots=True)
class AdaFaceConfig:
    """Everything about the recogniser a deployment gets to choose.

    Note the absence of any threshold: this adapter has no notion of a match,
    so there is nothing here to tune towards one.
    """

    model_path: Path
    expected_sha256: str | None = None
    model_name: str = "adaface_ir101_webface12m"
    device: str = "cpu"
    batch_size: int = 16

    def __post_init__(self) -> None:
        """Validate the batch size."""
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {self.batch_size}")


class AdaFaceRecognizer:
    """Produces face embeddings. Implements ``ModelAdapter``."""

    def __init__(self, config: AdaFaceConfig) -> None:
        """Record configuration. Weights are loaded by ``warmup()``."""
        self._config = config
        self._model: Backbone | None = None
        self._model_version: str | None = None

    @property
    def model_name(self) -> str:
        """Identifier of the recognition model."""
        return self._config.model_name

    @property
    def model_version(self) -> str:
        """Content hash of the loaded weights."""
        if self._model_version is None:
            raise RecognitionError("model version is unknown until warmup() has loaded the weights")
        return self._model_version

    @property
    def preprocessing_version(self) -> str:
        """Version of the preprocessing that produced the crops this consumes.

        Recognition inherits the detector's alignment: the crop geometry *is*
        the preprocessing, so both adapters must report the same value for an
        embedding's provenance to mean anything.
        """
        return PREPROCESSING_VERSION

    @property
    def provenance(self) -> EmbeddingProvenance:
        """The provenance stamped onto every embedding this adapter produces."""
        return EmbeddingProvenance(
            model_name=self.model_name,
            model_version=self.model_version,
            preprocessing_version=self.preprocessing_version,
        )

    def warmup(self) -> None:
        """Verify and load the weights, then run one inference. Idempotent."""
        if self._model is not None:
            return

        path = self._config.model_path
        if not path.is_file():
            raise ModelIntegrityError(f"recognition weights not found at {path}")

        digest = file_sha256(path)
        expected = self._config.expected_sha256
        if expected is not None and digest != expected:
            raise ModelIntegrityError(
                f"recognition weights at {path} hash {digest}, expected {expected}"
            )
        if expected is None:
            logger.warning(
                "loading recognition weights without an expected checksum",
                extra={"model_path": str(path), "model_sha256": digest},
            )

        model = ir_101(output_dim=EMBEDDING_DIM)
        state_dict = self._backbone_state_dict(path)
        # Strict: a checkpoint that does not fit the published architecture is
        # an error, never something to paper over with partial loading.
        model.load_state_dict(state_dict, strict=True)
        model.eval()
        model.to(self._config.device)

        self._model = model
        self._model_version = digest
        self.embed_batch([np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)])
        logger.info(
            "recognition model loaded",
            extra={
                "model_name": self.model_name,
                "model_version": digest,
                "preprocessing_version": self.preprocessing_version,
            },
        )

    @staticmethod
    def _backbone_state_dict(path: Path) -> dict[str, torch.Tensor]:
        """Read the checkpoint and strip its wrapper prefix."""
        raw = load_file(str(path))
        stripped = {
            key[len(_CHECKPOINT_PREFIX) :]: value
            for key, value in raw.items()
            if key.startswith(_CHECKPOINT_PREFIX)
        }
        if not stripped:
            raise ModelIntegrityError(
                f"no backbone parameters found in {path}: expected keys prefixed "
                f"with {_CHECKPOINT_PREFIX!r}, found e.g. {sorted(raw)[:3]}"
            )
        return stripped

    def embed(self, face: AlignedFace | UInt8Array) -> FaceEmbedding:
        """Return the embedding of one aligned face crop."""
        return self.embed_batch([face])[0]

    def embed_batch(self, faces: Sequence[AlignedFace | UInt8Array]) -> list[FaceEmbedding]:
        """Embed several aligned crops, preserving input order.

        A person has many samples; embedding is expressed over a collection so
        callers are never nudged towards treating one crop as canonical.
        """
        if not faces:
            return []

        # Validate before loading: a malformed crop should not cost the time
        # and memory of pulling a 260MB model off disk first.
        crops = [self._as_crop(face) for face in faces]
        self.warmup()
        provenance = self.provenance

        vectors: list[np.ndarray] = []
        for start in range(0, len(crops), self._config.batch_size):
            chunk = crops[start : start + self._config.batch_size]
            vectors.extend(self._infer(chunk))

        return [
            FaceEmbedding(vector=vector.astype(np.float32), provenance=provenance)
            for vector in vectors
        ]

    def _as_crop(self, face: AlignedFace | UInt8Array) -> UInt8Array:
        """Validate an input and return its pixel data.

        Accepting ``AlignedFace`` is the intended path: it guarantees the crop
        came from the alignment this model expects, and lets us refuse crops
        produced by a different preprocessing version.
        """
        if isinstance(face, AlignedFace):
            if face.preprocessing_version != self.preprocessing_version:
                raise RecognitionError(
                    f"crop was produced by preprocessing {face.preprocessing_version!r}, "
                    f"but this model expects {self.preprocessing_version!r}"
                )
            crop = face.image
        else:
            crop = face

        if crop.ndim != 3 or crop.shape[2] != 3:
            raise RecognitionError(f"expected an HxWx3 BGR crop, got shape {crop.shape}")
        if crop.dtype != np.uint8:
            raise RecognitionError(f"expected a uint8 crop, got {crop.dtype}")
        if crop.shape[0] != INPUT_SIZE or crop.shape[1] != INPUT_SIZE:
            raise RecognitionError(
                f"expected a {INPUT_SIZE}x{INPUT_SIZE} aligned crop, got "
                f"{crop.shape[0]}x{crop.shape[1]}"
            )
        return crop

    def _infer(self, crops: list[UInt8Array]) -> list[np.ndarray]:
        if self._model is None:
            raise RecognitionError("model is not loaded")

        # BGR to RGB, then scale to [-1, 1] as the published models expect.
        batch = np.stack([crop[:, :, ::-1] for crop in crops]).astype(np.float32)
        batch = (batch - 127.5) / 127.5
        tensor = torch.from_numpy(batch.transpose(0, 3, 1, 2)).to(self._config.device)

        with torch.inference_mode():
            outputs = self._model(tensor)
            # Normalise so cosine similarity is a plain dot product, and so
            # vector length cannot masquerade as confidence.
            normalised = torch.nn.functional.normalize(outputs, p=2.0, dim=1)
        return [row for row in normalised.cpu().numpy()]


def cosine_similarity(first: FaceEmbedding, second: FaceEmbedding) -> float:
    """Cosine similarity between two embeddings, in [-1, 1].

    This is a measurement, not a decision. It is deliberately *not* mapped onto
    a probability: that would imply a calibrated model and a known population
    prior, and we have neither. Turning this number into an identity claim is
    the job of the identity-decision layer, which owns the thresholds.
    """
    if not first.provenance.matches(second.provenance):
        raise IncomparableEmbeddingsError(
            f"cannot compare embeddings from {first.provenance} and {second.provenance}"
        )
    return float(np.clip(np.dot(first.vector, second.vector), -1.0, 1.0))
