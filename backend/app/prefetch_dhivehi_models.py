"""Download every public specialist artifact at its pinned revision."""

from __future__ import annotations

import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download

from app.services.dhivehi_models import MODEL_REGISTRY, DhivehiTask


def main() -> None:
    """Materialize immutable snapshots and a machine-readable receipt."""
    cache_dir = Path(os.getenv("DHIVEHI_AI_CACHE_DIR", "/srv/dhivehi-model-cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    skipped = {DhivehiTask.EMBEDDING, DhivehiTask.UNDERSTANDING}
    receipt: list[dict[str, str]] = []
    for task, spec in MODEL_REGISTRY.items():
        if task in skipped or spec.status != "available":
            continue
        path = snapshot_download(
            repo_id=spec.model_id,
            revision=spec.revision,
            cache_dir=cache_dir,
        )
        receipt.append(
            {
                "task": task.value,
                "model": spec.model_id,
                "revision": spec.revision,
                "path": path,
            }
        )
        print(f"ready {task.value}: {spec.model_id}@{spec.revision}", flush=True)
    (cache_dir / "eagleeye-dhivehi-models.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
