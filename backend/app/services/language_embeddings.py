"""Configurable multilingual sentence embeddings for Dhivehi semantic search."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class LanguageEmbedding:
    """A normalized dense vector and the model provenance that produced it."""

    vector: NDArray[np.float32]
    model: str
    version: str

    @property
    def dimension(self) -> int:
        """Return vector width."""
        return int(self.vector.shape[0])


class LanguageEmbedder(Protocol):
    """Embedding seam shared by the API, worker and tests."""

    model_name: str
    model_version: str

    async def embed_query(self, text: str) -> LanguageEmbedding:
        """Embed one search query."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[LanguageEmbedding]:
        """Embed documents as normalized vectors."""
        ...


def chunk_text(text: str, *, max_chars: int = 1_600, overlap: int = 200) -> list[str]:
    """Split long multilingual text on nearby whitespace with bounded overlap.

    Character bounds are deliberately conservative for multilingual E5's
    512-token window, including Thaana where tokenizer ratios vary.
    """
    if max_chars < 1 or overlap < 0 or overlap >= max_chars:
        raise ValueError("chunk bounds require 0 <= overlap < max_chars")
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + max_chars // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


class MultilingualE5Embedder:
    """SentenceTransformers adapter using E5 query/passage instructions."""

    def __init__(
        self,
        model_name_or_path: str,
        *,
        model_version: str,
        device: str = "cpu",
        batch_size: int = 16,
        max_tokens: int = 512,
    ) -> None:
        """Load a pinned model from a local path or Hugging Face identifier."""
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name_or_path
        self.model_version = model_version
        self._batch_size = batch_size
        revision = None if Path(model_name_or_path).exists() else model_version
        self._model = SentenceTransformer(
            model_name_or_path,
            device=device,
            revision=revision,
        )
        self._model.max_seq_length = max_tokens

    async def embed_query(self, text: str) -> LanguageEmbedding:
        """Embed a query without blocking the event loop."""
        vectors = await asyncio.to_thread(self._encode, [f"query: {text}"])
        return self._wrap(vectors[0])

    async def embed_documents(self, texts: list[str]) -> list[LanguageEmbedding]:
        """Embed passages without blocking the worker's control loop."""
        if not texts:
            return []
        vectors = await asyncio.to_thread(self._encode, [f"passage: {text}" for text in texts])
        return [self._wrap(vector) for vector in vectors]

    def _encode(self, texts: list[str]) -> NDArray[np.float32]:
        encoded = self._model.encode(
            texts,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(encoded, dtype=np.float32)

    def _wrap(self, vector: NDArray[np.float32]) -> LanguageEmbedding:
        return LanguageEmbedding(
            vector=np.asarray(vector, dtype=np.float32),
            model=self.model_name,
            version=self.model_version,
        )
