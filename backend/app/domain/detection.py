"""Value objects produced by face detection.

Plain geometry and scores: no model, no framework, no storage. A detector
reports what it saw; deciding what that means about a person's identity is not
its job and happens elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

#: Landmark order produced by SCRFD and expected by the alignment template.
LANDMARK_NAMES = ("left_eye", "right_eye", "nose", "left_mouth", "right_mouth")


class DetectionError(Exception):
    """Detection could not be performed on the given input."""


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """An axis-aligned box in pixel coordinates of the original image."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        """Reject boxes that do not enclose any area."""
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(f"degenerate bounding box: {self}")

    @property
    def width(self) -> float:
        """Box width in pixels."""
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        """Box height in pixels."""
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        """Box area in square pixels."""
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class FaceLandmarks:
    """The five facial keypoints, in ``LANDMARK_NAMES`` order.

    Stored as a ``(5, 2)`` float array of pixel coordinates in the original
    image.
    """

    points: npt.NDArray[np.float32]

    def __post_init__(self) -> None:
        """Validate the keypoint array's shape."""
        if self.points.shape != (5, 2):
            raise ValueError(
                f"expected 5 landmark points of 2 coordinates, got {self.points.shape}"
            )

    def as_dict(self) -> dict[str, tuple[float, float]]:
        """Return the keypoints keyed by name, for serialisation and logging."""
        return {
            name: (float(x), float(y))
            for name, (x, y) in zip(LANDMARK_NAMES, self.points, strict=True)
        }


@dataclass(frozen=True, slots=True)
class DetectedFace:
    """One face located in an image.

    ``score`` is the detector's confidence that this region is a face. It says
    nothing about *whose* face it is.
    """

    box: BoundingBox
    score: float
    landmarks: FaceLandmarks

    def __post_init__(self) -> None:
        """Validate the confidence range."""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"detection score must be in [0, 1], got {self.score}")


@dataclass(frozen=True, slots=True)
class AlignedFace:
    """A detected face warped to the canonical crop recognition expects.

    ``preprocessing_version`` travels with the crop so that the embedding
    computed from it can record exactly how it was produced.
    """

    detection: DetectedFace
    image: npt.NDArray[np.uint8]
    preprocessing_version: str

    def __post_init__(self) -> None:
        """Validate the crop's shape and dtype."""
        if self.image.ndim != 3 or self.image.shape[2] != 3:
            raise ValueError(f"aligned crop must be HxWx3, got shape {self.image.shape}")
        if self.image.dtype != np.uint8:
            raise ValueError(f"aligned crop must be uint8, got {self.image.dtype}")
