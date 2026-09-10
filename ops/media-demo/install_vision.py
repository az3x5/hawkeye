"""Install the pinned, permissively licensed vision demonstration models."""

from __future__ import annotations

import json
from pathlib import Path

from huggingface_hub import snapshot_download

CACHE = Path("/cache")
MODELS = (
    {
        "task": "object_detection",
        "model": "PekingU/rtdetr_r50vd",
        "revision": "df939e661d8c52e80608d1ec566561aabd25a4e7",
        "license": "apache-2.0",
    },
    {
        "task": "image_embedding",
        "model": "google/siglip2-base-patch16-384",
        "revision": "f775b65a79762255128c981547af89addcfe0f88",
        "license": "apache-2.0",
    },
    {
        "task": "image_video_segmentation",
        "model": "facebook/sam2-hiera-small",
        "revision": "e080ada8afd19df5e165abe71b006edc7f4c3d4e",
        "license": "apache-2.0",
    },
)


def main() -> None:
    """Materialize immutable snapshots and write a deployment receipt."""
    receipt: list[dict[str, str]] = []
    for spec in MODELS:
        print(f"installing {spec['task']}: {spec['model']}", flush=True)
        path = snapshot_download(
            repo_id=spec["model"],
            revision=spec["revision"],
            cache_dir=CACHE / "hub",
        )
        receipt.append({**spec, "path": path})
        print(f"ready {spec['model']}@{spec['revision']}", flush=True)
    target = CACHE / "vision-models.json"
    target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"receipt: {target}", flush=True)


if __name__ == "__main__":
    main()
