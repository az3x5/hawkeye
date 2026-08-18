# Person Intelligence — Face ID module

Face detection, recognition and identity resolution for the multimodal Person
Intelligence platform.

**Status: Phase 2 (SCRFD detection) complete.** Detection runs; recognition,
enrolment and matching do not exist yet — see
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for exactly what
is and is not built, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
design the phases build toward.

## Stack

| Concern | Technology |
| --- | --- |
| Backend | FastAPI |
| Face detection | SCRFD via onnxruntime |
| Face recognition | AdaFace (not yet implemented) |
| Metadata | PostgreSQL |
| Embeddings | Qdrant |
| Jobs | Redis |
| Frontend | Next.js + TypeScript (not yet implemented) |

## Layout

```
backend/
  app/
    api/v1/      HTTP layer: routers, request/response schemas
    core/        config, logging, error envelope, readiness registry
    adapters/    model adapter seam — the only route to AI models
      scrfd.py, preprocessing.py, factory.py
    connectors/  storage connector seam — the only route to storage providers
      postgres/  metadata store: tables, connector, repositories
    domain/      entities and repository interfaces (no ORM, no HTTP)
  migrations/    Alembic revisions
  tests/
docs/
docker-compose.yml
```

## Running locally

```bash
cp .env.example .env    # then edit; .env is git-ignored
```

```bash
docker compose up -d --build
```

The API is published on `127.0.0.1:8000`, and Postgres on `127.0.0.1:5432` for
local migrations and integration tests — remove that mapping outside local
development. Redis and Qdrant are reachable on the internal `faceid` network
exclusively; Qdrant holds biometric embeddings and is never published to a host
or public port.

Apply migrations:

```bash
docker compose exec api alembic upgrade head
```

Check it is up:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

### Development without Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -e 'backend[dev]'
```

Run the checks from the `backend/` directory:

```bash
PYTHONPATH=. ../.venv/bin/pytest -q && ../.venv/bin/ruff check . && ../.venv/bin/mypy
```

Detection tests are skipped unless the weights are present, and database tests
unless a real database is named, so neither can pass silently against nothing:

```bash
FACEID_TEST_POSTGRES_DSN=postgresql://faceid:$POSTGRES_PASSWORD@127.0.0.1:5432/faceid PYTHONPATH=. ../.venv/bin/pytest -q
```

## Face detection

Weights are **never committed and never baked into the image**. Fetch the SCRFD
ONNX file into `./models/` (git-ignored), pin its checksum, and the adapter
verifies it before every load — refusing to start on a mismatch:

```bash
FACEID_SCRFD_MODEL_PATH=/srv/models/scrfd_10g_bnkps.onnx
FACEID_SCRFD_MODEL_SHA256=5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91
```

`docker-compose.yml` mounts `./models` read-only at `/srv/models`.

Detection thresholds are configuration, not constants — tune them per
deployment and population:

| Setting | Default | Meaning |
| --- | --- | --- |
| `FACEID_SCRFD_SCORE_THRESHOLD` | `0.5` | Minimum confidence for a detection |
| `FACEID_SCRFD_NMS_IOU_THRESHOLD` | `0.4` | Overlap above which duplicates are suppressed |
| `FACEID_SCRFD_INPUT_SIZE` | `640` | Network input side, a multiple of 32 |

The detector reports `model_name`, `model_version` (the weights' own SHA-256)
and `preprocessing_version`, so any embedding derived from a crop can record
exactly what produced it. It is not wired into the API process: detection is
expected to run in a worker, and loading weights into every web process before
anything uses them would be waste.

## Configuration

All settings come from the environment with the `FACEID_` prefix (see
`.env.example`). `FACEID_POSTGRES_DSN`, `FACEID_REDIS_DSN` and
`FACEID_QDRANT_URL` are required; the service refuses to start without them.
No credential is defaulted in code and `.env` is git-ignored.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Liveness. Performs no I/O. |
| GET | `/api/v1/readyz` | Runs every registered dependency probe; 503 if any fails. |

Interactive docs are served at `/docs` in the `local` environment only.

Persistence is not yet reachable over HTTP — the domain and its repositories
exist, but no enrolment or lookup endpoint has been built.

## Data model

| Table | Holds |
| --- | --- |
| `persons` | `person_uuid`, the sole internal key |
| `person_external_identifiers` | upstream `id` / `local_id`, unique per `(source, kind, value)` |
| `face_samples` | many captures per person, deduplicated per person by content hash |
