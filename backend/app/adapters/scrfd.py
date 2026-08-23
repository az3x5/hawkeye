"""SCRFD face detection through onnxruntime.

The adapter owns the model session and nothing else: it takes an image and
returns geometry. It knows nothing about HTTP, people, or storage.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort

from app.adapters.preprocessing import (
    PREPROCESSING_VERSION,
    FloatArray,
    UInt8Array,
    align_face,
    anchor_centers,
    distance2bbox,
    distance2kps,
    letterbox,
    nms,
    to_blob,
)
from app.domain.detection import (
    AlignedFace,
    BoundingBox,
    DetectedFace,
    DetectionError,
    FaceLandmarks,
)

logger = logging.getLogger(__name__)

#: Feature-map strides of the SCRFD detection heads, coarsest last.
STRIDES = (8, 16, 32)

#: Anchors per feature-map cell, fixed by the network architecture.
ANCHORS_PER_CELL = 2

#: Bytes read per chunk when hashing the weights file.
_HASH_CHUNK = 1 << 20


class ModelIntegrityError(Exception):
    """The weights on disk are not the weights we were configured to load."""


@dataclass(frozen=True, slots=True)
class SCRFDConfig:
    """Everything about a detector that a deployment gets to choose.

    Thresholds live here rather than as constants in code: they are policy,
    they differ per deployment and population, and a value baked into a
    function cannot be reviewed or explained after a contested decision.
    """

    model_path: Path
    expected_sha256: str | None = None
    score_threshold: float = 0.5
    nms_iou_threshold: float = 0.4
    input_size: tuple[int, int] = (640, 640)
    model_name: str = "scrfd_10g_bnkps"
    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    # Thread counts are deployment policy, not a property of the model. Left
    # unset, onnxruntime sizes its pools to every core on the machine, so two
    # processes on one host each try to own the whole CPU and fight for it.
    intra_op_threads: int | None = None
    inter_op_threads: int | None = None

    def __post_init__(self) -> None:
        """Validate the ranges the detector cannot sensibly run outside."""
        for name in ("intra_op_threads", "inter_op_threads"):
            threads = getattr(self, name)
            if threads is not None and threads < 1:
                raise ValueError(f"{name} must be at least 1, got {threads}")
        if not self.providers:
            raise ValueError("at least one execution provider is required")
        if not 0.0 < self.score_threshold <= 1.0:
            raise ValueError(f"score_threshold must be in (0, 1], got {self.score_threshold}")
        if not 0.0 < self.nms_iou_threshold <= 1.0:
            raise ValueError(f"nms_iou_threshold must be in (0, 1], got {self.nms_iou_threshold}")
        width, height = self.input_size
        if width % 32 or height % 32:
            raise ValueError(
                f"input_size must be a multiple of 32 in both dimensions, got {self.input_size}"
            )


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class SCRFDDetector:
    """Detects faces and produces aligned crops. Implements ``ModelAdapter``."""

    def __init__(self, config: SCRFDConfig) -> None:
        """Record configuration. Weights are loaded by ``warmup()``."""
        self._config = config
        self._session: ort.InferenceSession | None = None
        self._model_version: str | None = None

    @property
    def model_name(self) -> str:
        """Identifier of the detection model."""
        return self._config.model_name

    @property
    def model_version(self) -> str:
        """Content hash of the loaded weights.

        Deriving the version from the bytes rather than a hand-written label
        means a swapped weights file cannot masquerade as the previous model.
        """
        if self._model_version is None:
            raise DetectionError("model version is unknown until warmup() has loaded the weights")
        return self._model_version

    @property
    def preprocessing_version(self) -> str:
        """Version of the preprocessing applied before inference."""
        return PREPROCESSING_VERSION

    def warmup(self) -> None:
        """Verify and load the weights, then run one inference to allocate.

        Idempotent: calling it again after a successful load does nothing.
        """
        if self._session is not None:
            return

        path = self._config.model_path
        if not path.is_file():
            raise ModelIntegrityError(f"detection weights not found at {path}")

        digest = file_sha256(path)
        expected = self._config.expected_sha256
        if expected is not None and digest != expected:
            raise ModelIntegrityError(
                f"detection weights at {path} hash {digest}, expected {expected}"
            )
        if expected is None:
            logger.warning(
                "loading detection weights without an expected checksum",
                extra={"model_path": str(path), "model_sha256": digest},
            )

        requested = list(self._config.providers)
        # onnxruntime falls back to CPU without complaint when a provider is
        # missing. That silence would let a deployment believe it is on the GPU
        # while it is not, so the mismatch is raised instead.
        available = set(ort.get_available_providers())
        missing = [provider for provider in requested if provider not in available]
        if missing:
            raise ModelIntegrityError(
                f"execution providers {missing} are not available in this build of "
                f"onnxruntime; available providers are {sorted(available)}"
            )

        options = ort.SessionOptions()
        if self._config.intra_op_threads is not None:
            options.intra_op_num_threads = self._config.intra_op_threads
        if self._config.inter_op_threads is not None:
            options.inter_op_num_threads = self._config.inter_op_threads
        session = ort.InferenceSession(str(path), sess_options=options, providers=requested)
        outputs = len(session.get_outputs())
        if outputs != len(STRIDES) * 3:
            raise ModelIntegrityError(
                f"expected {len(STRIDES) * 3} model outputs (score, bbox and keypoint "
                f"heads for {len(STRIDES)} strides), got {outputs}"
            )

        self._session = session
        self._model_version = digest
        width, height = self._config.input_size
        self._infer(np.zeros((1, 3, height, width), dtype=np.float32))
        logger.info(
            "detection model loaded",
            extra={
                "model_name": self.model_name,
                "model_version": digest,
                "preprocessing_version": self.preprocessing_version,
            },
        )

    def detect(self, image: UInt8Array) -> list[DetectedFace]:
        """Return every face found in a BGR image, most confident first."""
        if image.ndim != 3 or image.shape[2] != 3:
            raise DetectionError(f"expected an HxWx3 BGR image, got shape {image.shape}")
        if image.dtype != np.uint8:
            raise DetectionError(f"expected a uint8 image, got {image.dtype}")
        if image.size == 0:
            raise DetectionError("cannot detect faces in an empty image")

        self.warmup()
        canvas, scale = letterbox(image, self._config.input_size)
        outputs = self._infer(to_blob(canvas))
        boxes, scores, keypoints = self._decode(outputs, canvas.shape[0], canvas.shape[1])

        if boxes.size == 0:
            return []

        # Back to original-image coordinates before suppression, so the IoU
        # threshold means the same thing regardless of input scaling.
        boxes /= scale
        keypoints /= scale

        kept = nms(boxes, scores, self._config.nms_iou_threshold)
        faces = []
        for index in kept:
            x1, y1, x2, y2 = (float(v) for v in boxes[index])
            if x2 <= x1 or y2 <= y1:
                continue
            faces.append(
                DetectedFace(
                    box=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    score=float(min(max(scores[index], 0.0), 1.0)),
                    landmarks=FaceLandmarks(points=keypoints[index]),
                )
            )
        return faces

    def align(self, image: UInt8Array, face: DetectedFace, *, size: int = 112) -> AlignedFace:
        """Warp one detected face onto the canonical recognition crop."""
        crop = align_face(image, face.landmarks.points, size=size)
        return AlignedFace(
            detection=face, image=crop, preprocessing_version=self.preprocessing_version
        )

    def detect_and_align(self, image: UInt8Array, *, size: int = 112) -> list[AlignedFace]:
        """Detect every face and return an aligned crop for each.

        Returns one entry per face: a person may appear in an image more than
        once, and no face is privileged over another.
        """
        return [self.align(image, face, size=size) for face in self.detect(image)]

    def _infer(self, blob: FloatArray) -> list[FloatArray]:
        if self._session is None:
            raise DetectionError("model session is not loaded")
        input_name = self._session.get_inputs()[0].name
        outputs: list[FloatArray] = self._session.run(None, {input_name: blob})
        return outputs

    def _decode(
        self, outputs: list[FloatArray], height: int, width: int
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Turn raw head outputs into boxes, scores and keypoints on the canvas.

        Outputs arrive grouped by head: scores for every stride, then box
        distances, then keypoint distances.
        """
        count = len(STRIDES)
        all_boxes, all_scores, all_keypoints = [], [], []

        for index, stride in enumerate(STRIDES):
            scores = outputs[index].reshape(-1)
            box_distances = outputs[index + count].reshape(-1, 4) * stride
            keypoint_distances = outputs[index + count * 2].reshape(-1, 10) * stride

            centers = anchor_centers(height // stride, width // stride, stride, ANCHORS_PER_CELL)
            if centers.shape[0] != scores.shape[0]:
                raise DetectionError(
                    f"stride {stride}: model produced {scores.shape[0]} predictions but the "
                    f"feature map implies {centers.shape[0]}"
                )

            selected = np.nonzero(scores >= self._config.score_threshold)[0]
            if selected.size == 0:
                continue

            all_scores.append(scores[selected])
            all_boxes.append(distance2bbox(centers[selected], box_distances[selected]))
            all_keypoints.append(distance2kps(centers[selected], keypoint_distances[selected]))

        if not all_boxes:
            empty = np.zeros((0, 4), dtype=np.float32)
            return empty, np.zeros((0,), dtype=np.float32), np.zeros((0, 5, 2), dtype=np.float32)

        return (
            np.concatenate(all_boxes).astype(np.float32),
            np.concatenate(all_scores).astype(np.float32),
            np.concatenate(all_keypoints).astype(np.float32),
        )
