"""Dhivehi language primitives and authenticated HTTP contracts."""

from __future__ import annotations

from uuid import UUID, uuid4

import numpy as np
import pytest

from app.api.v1.language import BotRequest, TextRequest, normalize
from app.core.config import Settings
from app.domain.auth import Principal, Scope
from app.domain.jobs import LanguageEmbeddingJob
from app.domain.language import (
    LanguageDocument,
    PrimaryScript,
    Script,
    TransliterationDirection,
)
from app.main import create_app
from app.services.language import normalize_text, transliterate
from app.services.language_embeddings import LanguageEmbedding, chunk_text
from app.services.language_search import (
    DocumentSubmission,
    LanguageDocumentService,
    LanguageSearchService,
)


def test_normalization_preserves_original_and_removes_unsafe_bidi_controls() -> None:
    result = normalize_text("ދިވެހި\u202e English\u00a0text")

    assert result.original == "ދިވެހި\u202e English\u00a0text"
    assert result.normalized == "ދިވެހި English text"
    assert result.primary_script is PrimaryScript.MIXED


def test_script_spans_retain_offsets_and_neutral_text() -> None:
    result = normalize_text("ދިވެހި + English 42")

    assert [(span.text, span.script) for span in result.spans] == [
        ("ދިވެހި", Script.THAANA),
        (" + ", Script.OTHER),
        ("English", Script.LATIN),
        (" ", Script.OTHER),
        ("42", Script.NUMBER),
    ]
    assert "".join(span.text for span in result.spans) == result.normalized


@pytest.mark.parametrize(
    ("source", "expected"),
    [("dhivehi", "ދިވެހި"), ("aharen", "އަހަރެން")],
)
def test_latin_to_thaana_baseline(source: str, expected: str) -> None:
    result = transliterate(source, TransliterationDirection.LATIN_TO_THAANA)

    assert result.output == expected
    assert result.warnings


def test_thaana_to_latin_baseline_preserves_english() -> None:
    result = transliterate("ދިވެހި + English", TransliterationDirection.THAANA_TO_LATIN)

    assert result.output == "dhivehi + English"
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("latin", "thaana"),
    [
        ("ii", "އީ"),
        ("uu", "އޫ"),
        ("ee", "އޭ"),
        ("oo", "އޯ"),
    ],
)
def test_phonemic_long_vowels_round_trip(latin: str, thaana: str) -> None:
    to_thaana = transliterate(latin, TransliterationDirection.LATIN_TO_THAANA)
    to_latin = transliterate(thaana, TransliterationDirection.THAANA_TO_LATIN)

    assert to_thaana.output == thaana
    assert to_latin.output == latin
    assert to_thaana.model_version == "dv-rules-v2"
    assert to_latin.model_version == "dv-rules-v2"


@pytest.mark.parametrize(("alias", "thaana"), [("ey", "އޭ"), ("oa", "އޯ")])
def test_legacy_long_vowel_aliases_remain_accepted(alias: str, thaana: str) -> None:
    result = transliterate(alias, TransliterationDirection.LATIN_TO_THAANA)

    assert result.output == thaana


@pytest.mark.asyncio
async def test_normalization_endpoint_returns_declared_contract() -> None:
    principal = Principal(
        token_uuid=uuid4(),
        subject="linguist@example.com",
        kind="user",
        scopes=frozenset({Scope.LANGUAGE}),
    )

    response = await normalize(TextRequest(text="ދިވެހި English"), principal)

    assert response.primary_script is PrimaryScript.MIXED
    assert response.normalizer_version == "dv-normalizer-v1"


def test_application_exposes_language_contract(settings: Settings) -> None:
    paths = create_app(settings).openapi()["paths"]

    assert "/api/v1/nlp/normalize" in paths
    assert "/api/v1/nlp/transliterate" in paths
    assert "/api/v1/nlp/documents" in paths
    assert "/api/v1/nlp/documents/{document_uuid}" in paths
    assert "/api/v1/nlp/search" in paths
    assert "/api/v1/nlp/models" in paths
    assert "/api/v1/nlp/infer" in paths
    assert "/api/v1/nlp/chat" in paths
    assert "/api/v1/nlp/speech/transcribe" in paths
    assert "/api/v1/nlp/ocr" in paths


def test_bot_request_requires_a_user_final_turn_and_bounded_context() -> None:
    request = BotRequest.model_validate(
        {"messages": [{"role": "user", "content": "ކިހިނެއް؟"}], "response_language": "dhivehi"}
    )

    assert request.messages[-1].role == "user"
    with pytest.raises(ValueError, match="final chat message"):
        BotRequest.model_validate(
            {"messages": [{"role": "assistant", "content": "invent a reply"}]}
        )


class FakeDocuments:
    def __init__(self) -> None:
        self.stored: dict[object, LanguageDocument] = {}

    async def add(self, document: LanguageDocument) -> tuple[LanguageDocument, bool]:
        existing = next(
            (
                item
                for item in self.stored.values()
                if item.source == document.source and item.content_sha256 == document.content_sha256
            ),
            None,
        )
        if existing is not None:
            return existing, False
        self.stored[document.document_uuid] = document
        return document, True

    async def commit(self) -> None:
        raise AssertionError("document and durable job must commit atomically")

    async def get_many(self, identifiers: list[object]) -> dict[object, LanguageDocument]:
        return {identifier: self.stored[identifier] for identifier in identifiers}


class FakeQueue:
    def __init__(self, documents: FakeDocuments) -> None:
        self.jobs: list[object] = []
        self._active: set[object] = set()

    async def enqueue(self, job: LanguageEmbeddingJob) -> None:
        identifier = job.document_uuid
        if identifier in self._active:
            return
        self._active.add(identifier)
        self.jobs.append(job)


@pytest.mark.asyncio
async def test_document_submission_is_normalized_idempotent_and_enqueued_once() -> None:
    documents = FakeDocuments()
    queue = FakeQueue(documents)
    service = LanguageDocumentService(documents, queue)  # type: ignore[arg-type]
    request = DocumentSubmission(
        title="Greeting",
        source="test",
        text="ދިވެހި\u202e English",
        attributes={"category": "example"},
    )

    first, created = await service.submit(request)
    repeated, repeated_created = await service.submit(request)

    assert created is True
    assert repeated_created is False
    assert repeated.document_uuid == first.document_uuid
    assert first.normalized_text == "ދިވެހި English"
    assert len(queue.jobs) == 1


def test_long_language_documents_are_chunked_with_bounded_overlap() -> None:
    text = " ".join(f"word-{index}" for index in range(500))

    chunks = chunk_text(text, max_chars=200, overlap=30)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 200 for chunk in chunks)
    assert chunks[0].split()[-1] in chunks[1]


class FakeEmbedder:
    model_name = "test-e5"
    model_version = "test-v1"

    async def embed_query(self, text: str) -> LanguageEmbedding:
        assert text == "ދިވެހި weather"
        return LanguageEmbedding(
            np.array([1.0, 0.0], dtype=np.float32), self.model_name, self.model_version
        )

    async def embed_documents(self, texts: list[str]) -> list[LanguageEmbedding]:
        return [await self.embed_query(text) for text in texts]


class FakeVectors:
    def __init__(self, identifiers: list[UUID]) -> None:
        self.identifiers = identifiers

    async def search(self, embedding: object, **options: object) -> list[object]:
        from app.connectors.qdrant.language import LanguageVectorMatch

        assert options == {"limit": 2, "source": None, "minimum_score": None}
        return [
            LanguageVectorMatch(self.identifiers[0], 0.91),
            LanguageVectorMatch(self.identifiers[1], 0.73),
        ]


@pytest.mark.asyncio
async def test_semantic_search_hydrates_results_in_vector_rank_order() -> None:
    documents = FakeDocuments()
    first = LanguageDocument(
        title="Weather",
        source="news",
        original_text="މޫސުމާ ބެހޭ ޚަބަރެއް",
        normalized_text="މޫސުމާ ބެހޭ ޚަބަރެއް",
        primary_script=PrimaryScript.THAANA,
        content_sha256="a" * 64,
        attributes={},
    )
    second = LanguageDocument(
        title="Forecast",
        source="archive",
        original_text="weather forecast",
        normalized_text="weather forecast",
        primary_script=PrimaryScript.LATIN,
        content_sha256="b" * 64,
        attributes={},
    )
    documents.stored = {first.document_uuid: first, second.document_uuid: second}
    service = LanguageSearchService(
        documents,  # type: ignore[arg-type]
        FakeVectors([first.document_uuid, second.document_uuid]),  # type: ignore[arg-type]
        FakeEmbedder(),
    )

    hits, model, version = await service.search(
        "ދިވެހި weather", limit=2, source=None, minimum_score=None
    )

    assert [hit.title for hit in hits] == ["Weather", "Forecast"]
    assert [hit.score for hit in hits] == [0.91, 0.73]
    assert (model, version) == ("test-e5", "test-v1")
