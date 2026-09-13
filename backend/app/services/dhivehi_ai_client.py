"""Typed client for the private Dhivehi inference process."""

from __future__ import annotations

from typing import Any, Literal

import httpx

from app.services.dhivehi_models import BotMessage, DhivehiTask


class DhivehiAIServiceError(RuntimeError):
    """The specialist service rejected or could not execute a request."""


class DhivehiAIClient:
    """Keep internal transport details out of authenticated API handlers."""

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        """Create a bounded pooled client for one internal service URL."""
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout_seconds)
        )

    async def close(self) -> None:
        """Release pooled internal connections."""
        await self._client.aclose()

    async def capabilities(self) -> list[dict[str, Any]]:
        """Return truthful deployment/model state."""
        response = await self._request("GET", "/v1/capabilities")
        value = response.get("capabilities", [])
        return value if isinstance(value, list) else []

    async def text(self, task: DhivehiTask, text: str) -> dict[str, str]:
        """Run an explicit text-to-text model."""
        response = await self._request("POST", "/v1/text", json={"task": task.value, "text": text})
        return {str(key): str(value) for key, value in response.items()}

    async def chat(
        self,
        messages: list[BotMessage],
        response_language: Literal["auto", "dhivehi", "english"],
    ) -> dict[str, str]:
        """Generate one response from the private local conversational model."""
        response = await self._request(
            "POST",
            "/v1/chat",
            json={"messages": messages, "response_language": response_language},
        )
        return {str(key): str(value) for key, value in response.items()}

    async def speech(self, content: bytes, content_type: str) -> dict[str, str]:
        """Transcribe an already bounded audio body."""
        response = await self._request(
            "POST", "/v1/speech", files={"file": ("speech", content, content_type)}
        )
        return {str(key): str(value) for key, value in response.items()}

    async def ocr(self, content: bytes, content_type: str) -> dict[str, str]:
        """Recognize an already bounded image body."""
        response = await self._request(
            "POST", "/v1/ocr", files={"file": ("image", content, content_type)}
        )
        return {str(key): str(value) for key, value in response.items()}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            detail = "Dhivehi AI service is unavailable"
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = str(exc.response.json().get("detail", detail))
                except (ValueError, AttributeError):
                    pass
            raise DhivehiAIServiceError(detail) from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise DhivehiAIServiceError("Dhivehi AI service returned an invalid response")
        return payload
