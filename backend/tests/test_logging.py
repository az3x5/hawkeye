"""Biometric material must never reach the logs."""

from __future__ import annotations

import json
import logging

from app.core.logging import SENSITIVE_KEYS, JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "enrolled", (), None)
    record.__dict__.update(extra)
    return record


def test_formatter_emits_json_with_context() -> None:
    payload = json.loads(JsonFormatter().format(_record(person_uuid="abc")))
    assert payload["message"] == "enrolled"
    assert payload["level"] == "INFO"
    assert payload["person_uuid"] == "abc"


def test_embedding_fields_are_dropped() -> None:
    payload = json.loads(
        JsonFormatter().format(_record(person_uuid="abc", embedding=[0.1, 0.2], vector=[0.3]))
    )
    assert "embedding" not in payload
    assert "vector" not in payload
    assert payload["person_uuid"] == "abc"


def test_every_sensitive_key_is_stripped() -> None:
    payload = json.loads(JsonFormatter().format(_record(**{k: "secret" for k in SENSITIVE_KEYS})))
    assert SENSITIVE_KEYS.isdisjoint(payload)


def test_configure_logging_reclaims_server_loggers() -> None:
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.handlers = [logging.NullHandler()]
    uvicorn_logger.propagate = False

    configure_logging()

    assert uvicorn_logger.handlers == []
    assert uvicorn_logger.propagate is True
    root_handlers = logging.getLogger().handlers
    assert len(root_handlers) == 1
    assert isinstance(root_handlers[0].formatter, JsonFormatter)
