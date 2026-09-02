"""Internal-only HTTP process for memory-heavy Dhivehi model inference."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.services.dhivehi_models import (
    DhivehiAISettings,
    DhivehiModelRuntime,
    DhivehiTask,
    ModelUnavailableError,
)


class TextInferenceRequest(BaseModel):
    """Bounded text and an explicit specialist operation."""

    task: DhivehiTask
    text: str = Field(min_length=1, max_length=20_000)


@lru_cache(maxsize=1)
def get_runtime() -> DhivehiModelRuntime:
    """Create one process-wide resource governor and model cache."""
    return DhivehiModelRuntime(DhivehiAISettings())


def create_dhivehi_ai_app() -> FastAPI:
    """Build the private inference application."""
    app = FastAPI(title="EagleEye Dhivehi AI", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, str | None]:
        runtime = get_runtime()
        return {
            "status": "ok",
            "loaded_task": runtime.loaded_task.value if runtime.loaded_task else None,
        }

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, object]:
        return {"capabilities": get_runtime().capabilities()}

    @app.post("/v1/text")
    def text_inference(body: TextInferenceRequest) -> dict[str, str]:
        try:
            return get_runtime().generate_text(body.task, body.text)
        except (ValueError, ModelUnavailableError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.post("/v1/speech")
    def speech(
        file: Annotated[UploadFile, File(description="WAV/FLAC/OGG speech, at most 50 MB")],
    ) -> dict[str, str]:
        body = file.file.read(50 * 1024 * 1024 + 1)
        if len(body) > 50 * 1024 * 1024:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "audio exceeds 50 MB")
        try:
            return get_runtime().transcribe(body)
        except (ValueError, ModelUnavailableError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.post("/v1/ocr")
    def ocr(
        file: Annotated[UploadFile, File(description="Image containing Thaana text")],
    ) -> dict[str, str]:
        body = file.file.read(25 * 1024 * 1024 + 1)
        if len(body) > 25 * 1024 * 1024:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "image exceeds 25 MB")
        try:
            return get_runtime().recognize_image(body)
        except (ValueError, ModelUnavailableError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return app


def get_asgi_app() -> FastAPI:
    """Uvicorn factory entry point."""
    return create_dhivehi_ai_app()
