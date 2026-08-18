"""Collection naming.

Vectors produced by different models or different preprocessing are not
comparable. Rather than relying on every query to remember that, the provenance
triple is baked into the collection name: incomparable vectors end up in
physically separate indexes and cannot be searched against one another.
"""

from __future__ import annotations

import re

from app.domain.recognition import EmbeddingProvenance

#: Qdrant accepts a broad range of names; we restrict to a conservative set.
_UNSAFE = re.compile(r"[^a-zA-Z0-9_]+")

#: How much of the model version (a SHA-256) to keep in the name.
_VERSION_CHARS = 16

COLLECTION_PREFIX = "face_embeddings"


def _slug(value: str) -> str:
    cleaned = _UNSAFE.sub("_", value.strip().lower()).strip("_")
    if not cleaned:
        raise ValueError(f"cannot derive a collection name component from {value!r}")
    return cleaned


def collection_name(provenance: EmbeddingProvenance) -> str:
    """Return the collection holding vectors produced under ``provenance``.

    Deterministic: the same triple always maps to the same collection, and any
    change to model or preprocessing maps to a different one.
    """
    return "__".join(
        (
            COLLECTION_PREFIX,
            _slug(provenance.model_name),
            _slug(provenance.model_version)[:_VERSION_CHARS],
            _slug(provenance.preprocessing_version),
        )
    )
