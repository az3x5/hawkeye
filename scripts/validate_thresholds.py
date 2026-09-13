#!/usr/bin/env python3
"""Measure identification error rates against the enrolled population.

Thresholds are policy, and policy needs evidence. This measures what the
deployed gallery actually does, so ``FACEID_DECISION_ACCEPT_THRESHOLD`` and
``FACEID_DECISION_REVIEW_THRESHOLD`` can be set from observed error rates
rather than from a guess that has never been checked.

What it measures is *open-set identification*, because that is what the system
does: a probe is searched against the whole gallery, and the question is not
"do these two faces match" but "does the best match in 18,000 people belong to
the right person". Those are different error rates, and the second one gets
worse as the gallery grows.

For each probe sample it records two numbers:

  genuine   the best score among *other* samples of the same person
  impostor  the best score among samples of every other person

From those, at a candidate threshold t:

  FNMR(t)   probes whose genuine best falls below t — a known person missed
  FPIR(t)   probes whose *top* candidate is the wrong person and reaches t

FPIR is the dangerous one, and it is deliberately rank-1: the system acts on
the best candidate only, so an impostor who scores above the threshold but
below the right person changes nothing. A probe whose person has no other
sample is an "unknown" probe — its own sample is excluded, so nobody in the
gallery is the right answer and any impostor above t is a false identification.
Only mated probes can contribute to FNMR, so the rates are computed over
different denominators and both are reported.

Run it from a host with network access to Qdrant (inside the api container is
the easy way):

    docker exec -i hawkeye-api-1 python - < scripts/validate_thresholds.py \\
        --qdrant-url http://qdrant:6333 --probes 2000

No vector ever leaves this process: it reports similarity scores and counts,
never embeddings.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

#: Neighbours to pull per probe. It only has to be deep enough to get past the
#: probe's own samples to the first impostor; the gallery has far more.
SEARCH_DEPTH = 32

#: Thresholds the report walks, chosen to bracket any plausible operating point.
GRID = [round(0.20 + 0.02 * step, 2) for step in range(36)]


class ValidationError(RuntimeError):
    """The measurement could not be completed and its result must not be used."""


@dataclass(frozen=True, slots=True)
class Probe:
    """One measured probe: how well its own person scored, and the best impostor."""

    person_uuid: str
    genuine: float | None
    impostor: float | None


def _post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST JSON and return the decoded response."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decoded: dict[str, Any] = json.load(response)
    except urllib.error.URLError as exc:
        raise ValidationError(f"qdrant request to {url} failed: {exc}") from exc
    return decoded


def _get(url: str, timeout: float) -> dict[str, Any]:
    """GET JSON and return the decoded response."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            decoded: dict[str, Any] = json.load(response)
    except urllib.error.URLError as exc:
        raise ValidationError(f"qdrant request to {url} failed: {exc}") from exc
    return decoded


def resolve_collection(base: str, name: str | None, timeout: float) -> str:
    """Return the collection to measure, or fail if the choice is ambiguous."""
    payload = _get(f"{base}/collections", timeout)
    names = [c["name"] for c in payload["result"]["collections"]]
    if name is not None:
        if name not in names:
            raise ValidationError(f"collection {name!r} not found; have {names}")
        return name
    if len(names) != 1:
        raise ValidationError(
            f"expected exactly one collection, found {len(names)}: {names}. "
            "Pass --collection to choose; mixing provenance would make the "
            "measured rates meaningless."
        )
    return names[0]


def load_index(base: str, collection: str, timeout: float) -> dict[str, str]:
    """Return sample_uuid -> person_uuid for the whole collection."""
    index: dict[str, str] = {}
    offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 4096,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        result = _post(f"{base}/collections/{collection}/points/scroll", body, timeout)[
            "result"
        ]
        for point in result["points"]:
            payload = point["payload"]
            index[str(payload["face_sample_uuid"])] = str(payload["person_uuid"])
        offset = result.get("next_page_offset")
        if offset is None:
            break
    if not index:
        raise ValidationError(f"collection {collection!r} holds no points to measure")
    return index


def measure_by_recommend(
    base: str,
    collection: str,
    probes: list[str],
    index: dict[str, str],
    timeout: float,
    progress: bool,
) -> list[Probe]:
    """Score each probe against the gallery using the stored vector itself.

    Qdrant's ``recommend`` takes point ids as the positive example, which keeps
    the vector inside the database: the probe is identified by id, and only
    scores come back.
    """
    measured: list[Probe] = []
    for position, sample_uuid in enumerate(probes, start=1):
        person_uuid = index[sample_uuid]
        result = _post(
            f"{base}/collections/{collection}/points/recommend",
            {
                "positive": [sample_uuid],
                "limit": SEARCH_DEPTH,
                "with_payload": True,
            },
            timeout,
        )["result"]

        genuine: float | None = None
        impostor: float | None = None
        for neighbour in result:
            neighbour_sample = str(neighbour["payload"]["face_sample_uuid"])
            if neighbour_sample == sample_uuid:
                continue
            score = float(neighbour["score"])
            if str(neighbour["payload"]["person_uuid"]) == person_uuid:
                if genuine is None or score > genuine:
                    genuine = score
            elif impostor is None or score > impostor:
                impostor = score
        measured.append(
            Probe(person_uuid=person_uuid, genuine=genuine, impostor=impostor)
        )

        if progress and position % 100 == 0:
            print(f"  measured {position}/{len(probes)} probes", file=sys.stderr)
    return measured


def is_false_identification(probe: Probe, threshold: float) -> bool:
    """True when the top candidate at this threshold is the wrong person.

    Rank-1, because that is what the system acts on: an impostor scoring above
    the threshold but below the subject's own mate loses, and nothing bad
    happens. When the probe has no mate, the gallery holds no right answer, so
    any impostor at or above the threshold is a false identification.
    """
    if probe.impostor is None or probe.impostor < threshold:
        return False
    return probe.genuine is None or probe.impostor > probe.genuine


def rates(measured: list[Probe], threshold: float) -> tuple[float, float, int, int]:
    """Return (FNMR, FPIR, mated probes, all probes) at one threshold."""
    mated = [p for p in measured if p.genuine is not None]
    missed = sum(1 for p in mated if p.genuine is not None and p.genuine < threshold)
    false_ids = sum(1 for p in measured if is_false_identification(p, threshold))
    fnmr = missed / len(mated) if mated else float("nan")
    fpir = false_ids / len(measured) if measured else float("nan")
    return fnmr, fpir, len(mated), len(measured)


def report(
    measured: list[Probe], gallery_people: int, gallery_samples: int
) -> dict[str, Any]:
    """Build the full report structure."""
    mated = [p.genuine for p in measured if p.genuine is not None]
    impostors = [p.impostor for p in measured if p.impostor is not None]

    grid = []
    for threshold in GRID:
        fnmr, fpir, mated_n, all_n = rates(measured, threshold)
        grid.append(
            {
                "threshold": threshold,
                "fnmr": round(fnmr, 5),
                "fpir": round(fpir, 5),
                "mated_probes": mated_n,
                "probes": all_n,
            }
        )

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return round(ordered[position], 4)

    return {
        "gallery": {"people": gallery_people, "samples": gallery_samples},
        "probes": {
            "measured": len(measured),
            "with_a_mate": len(mated),
            "genuine_percentiles": {
                "p01": percentile(mated, 0.01),
                "p05": percentile(mated, 0.05),
                "p50": percentile(mated, 0.50),
            },
            "impostor_percentiles": {
                "p50": percentile(impostors, 0.50),
                "p95": percentile(impostors, 0.95),
                "p99": percentile(impostors, 0.99),
                "max": round(max(impostors), 4) if impostors else None,
            },
        },
        "grid": grid,
        "raw": [
            {"genuine": p.genuine, "impostor": p.impostor, "person_uuid": p.person_uuid}
            for p in measured
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Measure the population and print a threshold report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default="http://qdrant:6333")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--probes", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json-out", default=None, help="Write the full report here.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.probes < 100:
        parser.error("at least 100 probes are needed for the rates to mean anything")

    base = args.qdrant_url.rstrip("/")
    try:
        collection = resolve_collection(base, args.collection, args.timeout)
        if not args.quiet:
            print(f"collection: {collection}", file=sys.stderr)
        index = load_index(base, collection, args.timeout)

        by_person: dict[str, list[str]] = defaultdict(list)
        for sample_uuid, person_uuid in index.items():
            by_person[person_uuid].append(sample_uuid)

        rng = random.Random(args.seed)
        population = sorted(index)
        probes = rng.sample(population, min(args.probes, len(population)))

        if not args.quiet:
            print(
                f"gallery: {len(by_person)} people, {len(index)} samples; "
                f"probing {len(probes)}",
                file=sys.stderr,
            )
        measured = measure_by_recommend(
            base, collection, probes, index, args.timeout, progress=not args.quiet
        )
    except ValidationError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 2

    result = report(measured, gallery_people=len(by_person), gallery_samples=len(index))

    print(json.dumps(result, indent=2))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
