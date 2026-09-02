"""Specialist Dhivehi model provenance and resource-governor contracts."""

from __future__ import annotations

from app.services.dhivehi_models import MODEL_REGISTRY, DhivehiTask


def test_every_public_specialist_model_is_pinned_and_licensed() -> None:
    for task, spec in MODEL_REGISTRY.items():
        if task is DhivehiTask.UNDERSTANDING:
            continue
        assert len(spec.revision) == 40
        assert spec.license in {"apache-2.0", "mit"}
        assert spec.status == "available"


def test_gated_understanding_model_is_reported_as_blocked() -> None:
    model = MODEL_REGISTRY[DhivehiTask.UNDERSTANDING]

    assert model.status == "blocked"
    assert model.license == "gated"
    assert "approval" in model.limitation.lower()


def test_capability_metadata_never_claims_unqualified_accuracy() -> None:
    for spec in MODEL_REGISTRY.values():
        assert spec.quality_summary
        assert spec.limitation
