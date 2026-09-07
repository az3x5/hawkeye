"""Internal-only HTTP process for memory-heavy Dhivehi model inference."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, model_validator

from app.services.dhivehi_models import (
    BotMessage,
    DhivehiAISettings,
    DhivehiModelRuntime,
    DhivehiTask,
    ModelUnavailableError,
)


class TextInferenceRequest(BaseModel):
    """Bounded text and an explicit specialist operation."""

    task: DhivehiTask
    text: str = Field(min_length=1, max_length=20_000)


class ChatMessageRequest(BaseModel):
    """One user-visible conversation turn; system prompts remain server-owned."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatInferenceRequest(BaseModel):
    """A bounded conversation submitted to the private local bot."""

    messages: list[ChatMessageRequest] = Field(min_length=1, max_length=20)
    response_language: Literal["auto", "dhivehi", "english"] = "auto"

    @model_validator(mode="after")
    def validate_conversation(self) -> ChatInferenceRequest:
        """Reject excessive context and require a user to own the final turn."""
        if self.messages[-1].role != "user":
            raise ValueError("the final chat message must have role user")
        if sum(len(message.content) for message in self.messages) > 20_000:
            raise ValueError("chat context exceeds 20,000 characters")
        return self


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

    @app.post("/v1/chat")
    def chat_inference(body: ChatInferenceRequest) -> dict[str, str]:
        try:
            messages: list[BotMessage] = [
                {"role": message.role, "content": message.content} for message in body.messages
            ]
            return get_runtime().chat(messages, body.response_language)
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
