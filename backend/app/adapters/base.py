"""Model adapter seam.

AI models are reached only through these interfaces so that model code stays
free of HTTP concerns and can be swapped per deployment. Concrete adapters
(SCRFD detection, AdaFace recognition) are introduced in later phases.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelAdapter(Protocol):
    """Common contract for every AI model binding."""

    @property
    def model_name(self) -> str:
        """Identifier of the model, e.g. ``scrfd_10g``."""
        ...

    @property
    def model_version(self) -> str:
        """Version of the model weights this adapter loaded."""
        ...

    @property
    def preprocessing_version(self) -> str:
        """Version of the preprocessing pipeline applied before inference."""
        ...

    def warmup(self) -> None:
        """Load weights and run any allocation needed before first use."""
        ...
