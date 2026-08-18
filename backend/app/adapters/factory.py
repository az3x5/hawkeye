"""Construction of model adapters from application settings.

Kept apart from the adapters themselves so that model code depends on its own
config object, not on how this application happens to be configured.
"""

from __future__ import annotations

from app.adapters.scrfd import SCRFDConfig, SCRFDDetector
from app.core.config import Settings


class DetectorNotConfiguredError(RuntimeError):
    """Detection was requested but no weights have been configured."""


def build_scrfd_config(settings: Settings) -> SCRFDConfig:
    """Translate application settings into a detector configuration."""
    if settings.scrfd_model_path is None:
        raise DetectorNotConfiguredError(
            "FACEID_SCRFD_MODEL_PATH is not set; face detection cannot run"
        )
    return SCRFDConfig(
        model_path=settings.scrfd_model_path,
        expected_sha256=settings.scrfd_model_sha256,
        score_threshold=settings.scrfd_score_threshold,
        nms_iou_threshold=settings.scrfd_nms_iou_threshold,
        input_size=(settings.scrfd_input_size, settings.scrfd_input_size),
    )


def build_detector(settings: Settings) -> SCRFDDetector:
    """Build a detector from settings. Weights load on first use or warmup()."""
    return SCRFDDetector(build_scrfd_config(settings))
