"""Pure image and tensor operations used by the detection adapter.

Everything here is a deterministic function of its arguments: no model, no
configuration, no I/O. That keeps the parts of detection that are easy to get
subtly wrong — coordinate scaling, anchor layout, box decoding, alignment —
independently testable.

The version below names the exact behaviour of these functions. It must be
bumped whenever a change here would alter the pixels a model sees, because
embeddings produced under different preprocessing are not comparable.
"""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

#: Identifies this preprocessing pipeline. Recorded alongside every embedding.
PREPROCESSING_VERSION = "scrfd-letterbox-arcface112-v1"

#: Canonical 5-point template for a 112x112 crop, as used by ArcFace/AdaFace.
ARCFACE_TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

FloatArray = npt.NDArray[np.float32]
UInt8Array = npt.NDArray[np.uint8]


def letterbox(image: UInt8Array, target: tuple[int, int]) -> tuple[UInt8Array, float]:
    """Resize ``image`` into a ``target`` canvas, preserving aspect ratio.

    Returns the padded canvas and the scale that was applied, so detections can
    be mapped back to original-image coordinates. Padding is bottom/right only,
    which keeps the mapping a pure division by the scale — no offset to forget.
    """
    target_width, target_height = target
    height, width = image.shape[:2]
    if height == 0 or width == 0:
        raise ValueError("cannot letterbox an empty image")

    scale = min(target_width / width, target_height / height)
    new_width, new_height = int(round(width * scale)), int(round(height * scale))
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

    canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
    canvas[:new_height, :new_width] = resized
    return canvas, scale


def to_blob(image: UInt8Array, *, mean: float = 127.5, scale: float = 1 / 128.0) -> FloatArray:
    """Convert an HxWx3 BGR image to the NCHW RGB float tensor SCRFD expects."""
    blob = cv2.dnn.blobFromImage(
        image, scale, image.shape[:2][::-1], (mean, mean, mean), swapRB=True
    )
    return blob.astype(np.float32)


def anchor_centers(height: int, width: int, stride: int, num_anchors: int) -> FloatArray:
    """Return the (x, y) centre of every anchor on one feature map.

    Ordering matches the flattened model output: row-major over the feature
    map, with ``num_anchors`` consecutive entries per cell.
    """
    y, x = np.mgrid[:height, :width][:2]
    centers = np.stack([x, y], axis=-1).astype(np.float32) * stride
    centers = centers.reshape(-1, 2)
    if num_anchors > 1:
        centers = np.repeat(centers, num_anchors, axis=0)
    return centers


def distance2bbox(centers: FloatArray, distances: FloatArray) -> FloatArray:
    """Decode per-anchor (left, top, right, bottom) distances into boxes."""
    x1 = centers[:, 0] - distances[:, 0]
    y1 = centers[:, 1] - distances[:, 1]
    x2 = centers[:, 0] + distances[:, 2]
    y2 = centers[:, 1] + distances[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1).astype(np.float32)


def distance2kps(centers: FloatArray, distances: FloatArray) -> FloatArray:
    """Decode per-anchor keypoint offsets into ``(N, 5, 2)`` coordinates."""
    offsets = distances.reshape(distances.shape[0], -1, 2)
    points = centers[:, None, :] + offsets
    return points.astype(np.float32)


def nms(boxes: FloatArray, scores: FloatArray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression. Returns kept indices, best score first."""
    if boxes.size == 0:
        return []
    if not 0.0 < iou_threshold <= 1.0:
        raise ValueError(f"iou_threshold must be in (0, 1], got {iou_threshold}")

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(x2 - x1, 0) * np.maximum(y2 - y1, 0)
    order = scores.argsort()[::-1]

    kept: list[int] = []
    while order.size > 0:
        best = int(order[0])
        kept.append(best)
        if order.size == 1:
            break
        rest = order[1:]
        inter_w = np.maximum(0.0, np.minimum(x2[best], x2[rest]) - np.maximum(x1[best], x1[rest]))
        inter_h = np.maximum(0.0, np.minimum(y2[best], y2[rest]) - np.maximum(y1[best], y1[rest]))
        intersection = inter_w * inter_h
        union = areas[best] + areas[rest] - intersection
        iou = np.where(union > 0, intersection / np.maximum(union, 1e-9), 0.0)
        order = rest[iou <= iou_threshold]
    return kept


def umeyama_similarity(source: FloatArray, target: FloatArray) -> FloatArray:
    """Least-squares similarity transform mapping ``source`` onto ``target``.

    Returns the 2x3 matrix accepted by ``cv2.warpAffine``. Implemented here
    rather than taken from a feature-matching routine so the result is exact
    and deterministic for the five-point case, with no RANSAC randomness.
    """
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError(
            f"expected matching (N, 2) point sets, got {source.shape} and {target.shape}"
        )

    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    source_centered, target_centered = source - source_mean, target - target_mean

    covariance = target_centered.T @ source_centered / source.shape[0]
    u, singular_values, vt = np.linalg.svd(covariance)

    correction = np.eye(2, dtype=np.float64)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        correction[1, 1] = -1.0

    rotation = u @ correction @ vt
    source_variance = source_centered.var(axis=0).sum()
    if source_variance <= 0:
        raise ValueError("source points are degenerate; cannot fit a similarity transform")
    scale = (singular_values * np.diag(correction)).sum() / source_variance

    matrix = np.zeros((2, 3), dtype=np.float32)
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = target_mean - scale * rotation @ source_mean
    return matrix


def align_face(image: UInt8Array, landmarks: FloatArray, *, size: int = 112) -> UInt8Array:
    """Warp a face onto the canonical template so crops are pose-normalised.

    Recognition models are trained on this geometry; feeding them raw boxes
    instead is the single most common cause of silently poor accuracy.
    """
    template = ARCFACE_TEMPLATE_112 * (size / 112.0)
    matrix = umeyama_similarity(landmarks.astype(np.float32), template.astype(np.float32))
    warped = cv2.warpAffine(image, matrix, (size, size), borderValue=0.0)
    return warped.astype(np.uint8)
