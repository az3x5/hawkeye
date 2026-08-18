# Person Intelligence — Face ID module

Face detection, recognition and identity resolution for the multimodal Person
Intelligence platform.

**Status: Phase 1 (Person & Face domain + persistence) complete.** No
detection, recognition, enrolment or matching exists yet — see
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for exactly what
is and is not built, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
design the phases build toward.

## Stack

| Concern | Technology |
| --- | --- |
| Backend | FastAPI |
| Face detection | SCRFD (not yet implemented) |
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

Database integration tests are skipped unless a real database is named
explicitly, so they can never pass silently against nothing:

```bash
FACEID_TEST_POSTGRES_DSN=postgresql://faceid:$POSTGRES_PASSWORD@127.0.0.1:5432/faceid PYTHONPATH=. ../.venv/bin/pytest -q
```

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
