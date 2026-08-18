"""The architectural seams stay free of framework and provider coupling."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import app.adapters
import app.connectors
from app.adapters.base import ModelAdapter
from app.connectors.base import StorageConnector

FORBIDDEN_IMPORTS = ("fastapi", "starlette")


def _package_sources(package: ModuleType) -> list[Path]:
    """Return every Python source file inside ``package``."""
    return sorted(
        path
        for root in package.__path__
        for path in Path(root).rglob("*.py")
        if path.name != "__init__.py"
    )


def test_model_adapter_declares_the_required_provenance_fields() -> None:
    required = {"model_name", "model_version", "preprocessing_version", "warmup"}
    assert required <= set(ModelAdapter.__protocol_attrs__)  # type: ignore[attr-defined]


def test_storage_connector_declares_provider_and_ping() -> None:
    assert {"provider", "ping"} <= set(StorageConnector.__protocol_attrs__)  # type: ignore[attr-defined]


def test_adapters_and_connectors_do_not_import_http_framework() -> None:
    sources = _package_sources(app.adapters) + _package_sources(app.connectors)
    assert sources, "expected at least one module in the seam packages"
    for path in sources:
        text = path.read_text()
        for forbidden in FORBIDDEN_IMPORTS:
            assert f"import {forbidden}" not in text, f"{path} imports {forbidden}"
