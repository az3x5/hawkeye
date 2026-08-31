"""Structured JSON logging.

Biometric material (face crops, embeddings, descriptors) must never reach
the logs. `SENSITIVE_KEYS` names the fields that are dropped from structured
log records; call sites are expected to pass identifiers, not vectors.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

SENSITIVE_KEYS = frozenset(
    {"embedding", "embeddings", "vector", "vectors", "descriptor", "image", "image_bytes"}
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line, minus sensitive fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record`` to a JSON line, omitting sensitive fields."""
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in SENSITIVE_KEYS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


#: Loggers that install their own handlers and would otherwise bypass ours.
_HIJACKED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

#: Third-party loggers held at WARNING regardless of the application level.
#: botocore in particular logs every S3 request and response at DEBUG,
#: including full headers and signing details. In local development that
#: buries our own lines under thousands of theirs, and anywhere else it writes
#: request metadata into the log for no operational benefit.
_NOISY_LOGGERS = ("boto3", "botocore", "s3transfer", "urllib3")


def configure_logging(level: int = logging.INFO) -> None:
    """Route all logging through the JSON formatter.

    Idempotent, and safe to call after a server (uvicorn) has installed its
    own handlers: those loggers are stripped and made to propagate to root so
    a single formatter governs every line the process emits.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _HIJACKED_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
