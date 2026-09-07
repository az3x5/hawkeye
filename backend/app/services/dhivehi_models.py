"""Pinned, resource-aware specialist models for Dhivehi inference.

The service deliberately keeps at most one generative model resident.  A
cyber-ai deployment has enough RAM for every artifact on disk, but not enough
VRAM for face recognition, two translation directions, OCR, ASR and an LLM at
the same time.  Serialized, lazy loading makes that constraint explicit.
"""

from __future__ import annotations

import gc
import io
import threading
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
import numpy as np
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DhivehiTask(StrEnum):
    """Stable specialist task identifiers shared with the public API."""

    LATIN_TO_THAANA = "latin_to_thaana"
    THAANA_TO_LATIN = "thaana_to_latin"
    DHIVEHI_TO_ENGLISH = "dhivehi_to_english"
    ENGLISH_TO_DHIVEHI = "english_to_dhivehi"
    SPEECH_TO_TEXT = "speech_to_text"
    OCR = "ocr"
    EMBEDDING = "embedding"
    UNDERSTANDING = "understanding"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Immutable deployment metadata for one model-backed task."""

    task: DhivehiTask
    model_id: str
    revision: str
    license: str
    runtime: str
    status: str = "available"
    quality_summary: str = "No independent production evaluation recorded."
    limitation: str = "Operator review is required for consequential use."

    def public_dict(self) -> dict[str, object]:
        """Return JSON-safe metadata without local paths or credentials."""
        return asdict(self)


MODEL_REGISTRY: dict[DhivehiTask, ModelSpec] = {
    DhivehiTask.LATIN_TO_THAANA: ModelSpec(
        DhivehiTask.LATIN_TO_THAANA,
        "Neobe/dhivehi-byt5-latin2thaana-v1",
        "3d534d7729a6d1d62f364a3eebcb875d03467d0c",
        "apache-2.0",
        "seq2seq",
        quality_summary="Specialized on roughly 150k general pairs plus 10k news headlines.",
        limitation="No formal held-out score is published; names and mixed English need review.",
    ),
    DhivehiTask.THAANA_TO_LATIN: ModelSpec(
        DhivehiTask.THAANA_TO_LATIN,
        "Neobe/dhivehi-byt5-thaana2latin-v1",
        "fea6b74b04cd083dad151bed9e7cf19a710840e7",
        "apache-2.0",
        "seq2seq",
        quality_summary="Specialized on roughly 180k pairs plus 10k news headlines.",
        limitation="News-domain model with no published held-out accuracy score.",
    ),
    DhivehiTask.DHIVEHI_TO_ENGLISH: ModelSpec(
        DhivehiTask.DHIVEHI_TO_ENGLISH,
        "Neobe/dhivehi-en-mt5-large-paragraph",
        "f1239cbb566c74f9ed76a7cc5d3859a2e6b14104",
        "apache-2.0",
        "seq2seq",
        quality_summary="Human-reference article benchmark: chrF 54.56, chrF++ 51.05.",
        limitation="Primarily news, press and Wikipedia; informal/technical text is out of domain.",
    ),
    DhivehiTask.ENGLISH_TO_DHIVEHI: ModelSpec(
        DhivehiTask.ENGLISH_TO_DHIVEHI,
        "Neobe/en-dhivehi-mt5-large-paragraph",
        "0869c21476d60038036191d314961a7e139540c9",
        "apache-2.0",
        "seq2seq",
        quality_summary="Human-reference article benchmark: chrF 51.25, chrF++ 42.82.",
        limitation="Primarily news, press and Wikipedia; output requires human review.",
    ),
    DhivehiTask.SPEECH_TO_TEXT: ModelSpec(
        DhivehiTask.SPEECH_TO_TEXT,
        "d3b4g/whisper-small-dv-corrected",
        "0c1011e01822a7dd7a81c6f9f17284d6f56455b6",
        "apache-2.0",
        "asr",
        quality_summary="Self-reported Common Voice 17 Dhivehi WER: 11.07%.",
        limitation="Accuracy is unverified for noisy calls, dialects and operational recordings.",
    ),
    DhivehiTask.OCR: ModelSpec(
        DhivehiTask.OCR,
        "alakxender/trocr-dv-diet-base-bert",
        "468627dceb88ed03842e6d1889484f6d3e50230e",
        "apache-2.0",
        "ocr",
        quality_summary="Public Dhivehi TrOCR artifact trained on Dhivehi image-text data.",
        limitation=(
            "Recognizes a supplied text crop; full-page layout detection is a separate stage."
        ),
    ),
    DhivehiTask.EMBEDDING: ModelSpec(
        DhivehiTask.EMBEDDING,
        "alakxender/e5-dhivehi-combined-mnr",
        "65ead4fb4e6c5f72ef08eeed13eb2006bfa5f7ad",
        "mit",
        "sentence-transformers",
        quality_summary="Fine-tuned on 184,780 Dhivehi similarity, QA and article pairs.",
        limitation="No independent retrieval benchmark is published; deploy to a new collection.",
    ),
    DhivehiTask.UNDERSTANDING: ModelSpec(
        DhivehiTask.UNDERSTANDING,
        "qwen3:4b",
        "ollama-library-qwen3-4b",
        "apache-2.0",
        "ollama",
        quality_summary=(
            "Public Qwen3 4B instruction model with multilingual conversational support."
        ),
        limitation=(
            "Not independently evaluated for Dhivehi; verify names, dates and consequential claims."
        ),
    ),
}


class DhivehiAISettings(BaseSettings):
    """Configuration owned by the isolated inference process."""

    model_config = SettingsConfigDict(env_prefix="DHIVEHI_AI_", extra="ignore", frozen=True)

    cache_dir: Path = Path("/srv/dhivehi-model-cache")
    embedding_cache_dir: Path = Path("/srv/language-model-cache/hub")
    device: str = "cpu"
    torch_threads: int = Field(default=8, ge=1, le=16)
    max_input_tokens: int = Field(default=1024, ge=32, le=4096)
    max_new_tokens: int = Field(default=512, ge=16, le=2048)
    bot_max_new_tokens: int = Field(default=1024, ge=64, le=4096)
    max_audio_seconds: int = Field(default=600, ge=1, le=3600)
    bot_url: str = "http://ollama:11434"
    bot_model: str = "qwen3:4b"
    bot_timeout_seconds: float = Field(default=300.0, ge=1.0, le=1800.0)


class BotMessage(TypedDict):
    """One validated conversational turn passed to the local model."""

    role: Literal["user", "assistant"]
    content: str


class ModelUnavailableError(RuntimeError):
    """A selected model cannot be used in this deployment."""


class DhivehiModelRuntime:
    """Serialize inference and retain only the most recently used model."""

    def __init__(self, settings: DhivehiAISettings) -> None:
        """Initialize the cache directory and serialized model slot."""
        self.settings = settings
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._loaded_task: DhivehiTask | None = None
        self._processor: Any = None
        self._model: Any = None

    @property
    def loaded_task(self) -> DhivehiTask | None:
        """Task whose weights currently occupy memory, if any."""
        return self._loaded_task

    def capabilities(self) -> list[dict[str, object]]:
        """Return truthful model availability and provenance."""
        loaded = self.loaded_task
        rows: list[dict[str, object]] = []
        for task, spec in MODEL_REGISTRY.items():
            row = spec.public_dict()
            if task is DhivehiTask.UNDERSTANDING:
                digest = self._bot_digest()
                row["status"] = "installed" if digest else "not_installed"
                if digest:
                    row["revision"] = digest
            elif spec.status == "blocked":
                row["status"] = "blocked"
            else:
                row["status"] = "installed" if self._artifact_installed(task) else "not_installed"
            row["loaded"] = task is loaded
            rows.append(row)
        return rows

    def _artifact_installed(self, task: DhivehiTask) -> bool:
        """Check the immutable snapshot rather than assuming a download succeeded."""
        if task is DhivehiTask.UNDERSTANDING:
            return self._bot_digest() is not None
        spec = MODEL_REGISTRY[task]
        root = (
            self.settings.embedding_cache_dir
            if task is DhivehiTask.EMBEDDING
            else self.settings.cache_dir
        )
        repository = "models--" + spec.model_id.replace("/", "--")
        return (root / repository / "snapshots" / spec.revision).is_dir()

    def chat(
        self,
        messages: list[BotMessage],
        response_language: Literal["auto", "dhivehi", "english"] = "auto",
    ) -> dict[str, str]:
        """Generate one bounded bot reply through the private Ollama service."""
        spec = MODEL_REGISTRY[DhivehiTask.UNDERSTANDING]
        if not self._artifact_installed(DhivehiTask.UNDERSTANDING):
            raise ModelUnavailableError(
                f"local bot model {self.settings.bot_model} is not installed"
            )
        wants_dhivehi = response_language == "dhivehi" or (
            response_language == "auto" and self._contains_thaana(messages[-1]["content"])
        )
        bridged_messages = self._bridge_thaana_messages(messages)
        language_instruction = (
            "Reply in clear English; your final answer will be translated into Dhivehi."
            if wants_dhivehi
            else "Reply in English unless the user explicitly requests another language."
        )
        system = (
            "You are EagleEye's Dhivehi intelligence assistant. Help with Dhivehi and English "
            "text, summaries, analysis and questions. Distinguish facts from inference, never "
            "invent intelligence records or claim access to data not included in the conversation, "
            "and say when evidence is insufficient. "
            + language_instruction
        )
        try:
            response = httpx.post(
                f"{self.settings.bot_url.rstrip('/')}/api/chat",
                json={
                    "model": self.settings.bot_model,
                    "messages": [{"role": "system", "content": system}, *bridged_messages],
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_ctx": 4096,
                        "num_predict": self.settings.bot_max_new_tokens,
                    },
                },
                timeout=self.settings.bot_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload.get("message", {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise ModelUnavailableError("local bot returned an empty response")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelUnavailableError("local Qwen bot is unavailable") from exc
        result_text = content.strip()
        model_name = str(payload.get("model", self.settings.bot_model))
        model_revision = self._bot_digest() or spec.revision
        if wants_dhivehi:
            translated = self.generate_text(DhivehiTask.ENGLISH_TO_DHIVEHI, result_text)
            result_text = translated["text"]
            model_name = f"{model_name} + {translated['model']}"
            model_revision = f"{model_revision}+{translated['model_revision']}"
        return {
            "task": DhivehiTask.UNDERSTANDING.value,
            "text": result_text,
            "model": model_name,
            "model_revision": model_revision,
            "quality_summary": spec.quality_summary,
            "limitation": (
                spec.limitation
                + (" Thaana replies use a specialist translation bridge." if wants_dhivehi else "")
            ),
        }

    def _bridge_thaana_messages(self, messages: list[BotMessage]) -> list[BotMessage]:
        """Translate Thaana turns so the general model reasons over reliable English."""
        bridged: list[BotMessage] = []
        for message in messages:
            content = message["content"]
            if self._contains_thaana(content):
                content = self.generate_text(DhivehiTask.DHIVEHI_TO_ENGLISH, content)["text"]
            bridged.append({"role": message["role"], "content": content})
        return bridged

    @staticmethod
    def _contains_thaana(text: str) -> bool:
        """Return whether text includes a Unicode Thaana code point."""
        return any("\u0780" <= character <= "\u07bf" for character in text)

    def _bot_digest(self) -> str | None:
        """Return the installed Ollama digest without downloading or guessing."""
        try:
            response = httpx.get(
                f"{self.settings.bot_url.rstrip('/')}/api/tags",
                timeout=min(self.settings.bot_timeout_seconds, 5.0),
            )
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError, TypeError):
            return None
        for model in models if isinstance(models, list) else []:
            if not isinstance(model, dict):
                continue
            name = model.get("name", model.get("model"))
            if name == self.settings.bot_model or name == f"{self.settings.bot_model}:latest":
                digest = model.get("digest")
                return str(digest) if digest else self.settings.bot_model
        return None

    def generate_text(self, task: DhivehiTask, text: str) -> dict[str, str]:
        """Run translation or transliteration under one serialized model lease."""
        if task not in {
            DhivehiTask.LATIN_TO_THAANA,
            DhivehiTask.THAANA_TO_LATIN,
            DhivehiTask.DHIVEHI_TO_ENGLISH,
            DhivehiTask.ENGLISH_TO_DHIVEHI,
        }:
            raise ValueError(f"{task} is not a text-to-text task")
        with self._lock:
            processor, model = self._ensure_loaded(task)
            inputs = processor(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=self.settings.max_input_tokens,
            )
            inputs = {key: value.to(self.settings.device) for key, value in inputs.items()}
            output = model.generate(
                **inputs,
                max_new_tokens=self.settings.max_new_tokens,
                num_beams=4 if "mt5" in MODEL_REGISTRY[task].model_id.lower() else 1,
            )
            return self._result(task, processor.decode(output[0], skip_special_tokens=True))

    def transcribe(self, audio_bytes: bytes) -> dict[str, str]:
        """Decode bounded audio and transcribe it to Thaana."""
        import librosa
        import soundfile as sf

        with self._lock:
            processor, model = self._ensure_loaded(DhivehiTask.SPEECH_TO_TEXT)
            samples, rate = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
            if samples.ndim == 2:
                samples = np.mean(samples, axis=1)
            duration = len(samples) / float(rate)
            if duration > self.settings.max_audio_seconds:
                raise ValueError(
                    f"audio is {duration:.1f}s; limit is {self.settings.max_audio_seconds}s"
                )
            if rate != 16_000:
                samples = librosa.resample(samples, orig_sr=rate, target_sr=16_000)
            features = processor(samples, sampling_rate=16_000, return_tensors="pt")
            input_features = features.input_features.to(self.settings.device)
            output = model.generate(input_features, max_new_tokens=self.settings.max_new_tokens)
            text = processor.batch_decode(output, skip_special_tokens=True)[0]
            return self._result(DhivehiTask.SPEECH_TO_TEXT, text)

    def recognize_image(self, image_bytes: bytes) -> dict[str, str]:
        """Recognize Thaana in one supplied text image or crop."""
        from PIL import Image

        with self._lock:
            processor, model = self._ensure_loaded(DhivehiTask.OCR)
            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("RGB")
            pixels = processor(images=image, return_tensors="pt").pixel_values.to(
                self.settings.device
            )
            output = model.generate(pixels, max_new_tokens=self.settings.max_new_tokens)
            text = processor.batch_decode(output, skip_special_tokens=True)[0]
            return self._result(DhivehiTask.OCR, text)

    def _ensure_loaded(self, task: DhivehiTask) -> tuple[Any, Any]:
        spec = MODEL_REGISTRY[task]
        if spec.status != "available":
            raise ModelUnavailableError(spec.limitation)
        if self._loaded_task is task:
            return self._processor, self._model
        self._unload()

        import torch
        from transformers import (
            AutoModelForSeq2SeqLM,
            AutoModelForSpeechSeq2Seq,
            AutoProcessor,
            AutoTokenizer,
            VisionEncoderDecoderModel,
        )

        torch.set_num_threads(self.settings.torch_threads)
        common = {"revision": spec.revision, "cache_dir": str(self.settings.cache_dir)}
        if spec.runtime == "seq2seq":
            processor = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
                spec.model_id, **common
            )
            model = AutoModelForSeq2SeqLM.from_pretrained(spec.model_id, **common)
        elif spec.runtime == "asr":
            processor = AutoProcessor.from_pretrained(  # type: ignore[no-untyped-call]
                spec.model_id, **common
            )
            model = AutoModelForSpeechSeq2Seq.from_pretrained(spec.model_id, **common)
        elif spec.runtime == "ocr":
            processor = AutoProcessor.from_pretrained(  # type: ignore[no-untyped-call]
                spec.model_id, **common
            )
            model = VisionEncoderDecoderModel.from_pretrained(  # type: ignore[no-untyped-call]
                spec.model_id, **common
            )
        else:
            raise ModelUnavailableError(f"runtime {spec.runtime} is not executable here")
        model = model.eval().to(self.settings.device)
        self._loaded_task, self._processor, self._model = task, processor, model
        return processor, model

    def _unload(self) -> None:
        self._processor = None
        self._model = None
        self._loaded_task = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    @staticmethod
    def _result(task: DhivehiTask, text: str) -> dict[str, str]:
        spec = MODEL_REGISTRY[task]
        return {
            "task": task.value,
            "text": text.strip(),
            "model": spec.model_id,
            "model_revision": spec.revision,
            "quality_summary": spec.quality_summary,
            "limitation": spec.limitation,
        }
