"""Check private model services; optionally run one synthetic, non-identifying summary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

import httpx


async def check(*, smoke: bool) -> dict[str, Any]:
    """Report service availability without exposing configured addresses or credentials."""
    ollama = os.getenv("EVIDENCE_OLLAMA_URL", "http://ollama:11434").rstrip("/")
    specialist = os.getenv("FACEID_DHIVEHI_AI_URL", "http://dhivehi-ai:8010").rstrip("/")
    summary = os.getenv("EVIDENCE_SUMMARY_MODEL", "qwen3:4b-instruct")
    vision = os.getenv("EVIDENCE_VISION_MODEL", "qwen3-vl:4b-instruct")
    report: dict[str, Any] = {"language_accuracy": "requires_reviewed_evaluation"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        try:
            response = await client.get(f"{ollama}/api/tags")
            response.raise_for_status()
            installed = {model["name"] for model in response.json().get("models", [])}
            report["summary_installed"] = summary in installed
            report["vision_installed"] = vision in installed
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            report["ollama"] = "unavailable_or_invalid_response"
        try:
            response = await client.get(f"{specialist}/v1/capabilities")
            response.raise_for_status()
            report["specialists"] = [
                {"task": item.get("task"), "status": item.get("status")}
                for item in response.json().get("capabilities", [])
            ]
        except (httpx.HTTPError, ValueError, TypeError):
            report["specialists"] = "unavailable_or_invalid_response"
        if smoke:
            try:
                response = await client.post(
                    f"{ollama}/api/chat",
                    timeout=180,
                    json={
                        "model": summary,
                        "stream": False,
                        "keep_alive": 0,
                        "options": {"temperature": 0, "num_predict": 128, "num_ctx": 2048},
                        "format": {
                            "type": "object",
                            "properties": {"quote": {"type": "string"}},
                            "required": ["quote"],
                            "additionalProperties": False,
                        },
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    'Return JSON with quote exactly "The meeting starts at nine." '
                                    'from this synthetic evidence: "The meeting starts at nine."'
                                ),
                            }
                        ],
                    },
                )
                response.raise_for_status()
                value = json.loads(response.json()["message"]["content"])
                report["synthetic_summary"] = (
                    "passed" if value.get("quote") == "The meeting starts at nine." else "failed"
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                report["synthetic_summary"] = "failed"
    return report


def main() -> None:
    """Print capability checks; smoke testing must be explicitly selected."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(check(smoke=args.smoke))
    print(json.dumps(report, indent=2))
    if not report.get("summary_installed") or not report.get("vision_installed"):
        raise SystemExit(1)
    if args.smoke and report.get("synthetic_summary") != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
