# Person Intelligence — Face ID module

Face detection, recognition and identity resolution for the multimodal Person
Intelligence platform.

**Status: Phase 11 (orphaned image reconciliation) complete.** Erasure spans
metadata, vectors and images, and housekeeping now reconciles leftovers on both
the vector and image sides — see
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for exactly what
is and is not built, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
design the phases build toward.

## Stack

| Concern | Technology |
| --- | --- |
| Backend | FastAPI |
| Face detection | SCRFD via onnxruntime |
| Face recognition | AdaFace IR-101 via PyTorch (CPU) |
| Metadata | PostgreSQL |
| Embeddings | Qdrant (one collection per model provenance) |
| Jobs | Redis |
| Frontend | Next.js 16 + React 19 + TypeScript (strict) |

## Layout

```
backend/
  app/
    api/v1/      HTTP layer: routers, request/response schemas
    core/        config, logging, error envelope, readiness registry
    adapters/    model adapter seam — the only route to AI models
      scrfd.py, adaface.py, iresnet.py, preprocessing.py, factory.py
    connectors/  storage connector seam — the only route to storage providers
      postgres/  metadata store: tables, connector, repositories
    domain/      entities and repository interfaces (no ORM, no HTTP)
    services/    orchestration: enrolment, identification
    worker.py    embedding worker process
  migrations/    Alembic revisions
  tests/
frontend/
  src/app/       review queue, review detail, runtime API proxy
  src/lib/       API client, types, formatting
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

Integration tests need the vector store reachable from the host; the dev
overlay publishes it on loopback (the base file deliberately does not):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

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
FACEID_TEST_POSTGRES_DSN=postgresql://faceid:$POSTGRES_PASSWORD@127.0.0.1:5432/faceid FACEID_TEST_QDRANT_URL=http://127.0.0.1:6333 FACEID_TEST_REDIS_DSN=redis://127.0.0.1:6379/15 PYTHONPATH=. ../.venv/bin/pytest -q
```

Note the Redis database index: the tests and the running `worker` service share
one Redis instance, and the worker will happily consume a test's job off the
default database. Database 15 keeps them apart.

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

## Face recognition

AdaFace IR-101 (WebFace12M), supplied and verified exactly like the detector's
weights:

```bash
FACEID_ADAFACE_MODEL_PATH=/srv/models/adaface_ir101_webface12m.safetensors
FACEID_ADAFACE_MODEL_SHA256=2ea535a43877bd3de8091903935c783ce335be66a9f8917fae9a7a18ae4bbf56
```

The backbone is re-implemented in `app/adapters/iresnet.py` rather than
executed from the weights repository — loading a checkpoint should not mean
running code fetched alongside it. `load_state_dict` is strict, so any
divergence from the published architecture fails loudly instead of producing
quietly wrong embeddings.

**There is no threshold here, by design.** Recognition returns L2-normalised
512-d embeddings and `cosine_similarity` returns a raw score in `[-1, 1]`. It
is never mapped onto a probability: that would imply a calibrated model and a
known population prior, and we have neither. Deciding whether a score means
"same person" belongs to the identity-decision layer, which does not exist yet.

Embeddings from different `(model_name, model_version, preprocessing_version)`
triples refuse to be compared — `IncomparableEmbeddingsError` — because such a
comparison degrades accuracy in a way nothing downstream would notice.

Install CPU-only torch; the default index ships multi-GB CUDA wheels:

```bash
.venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -e 'backend[dev]'
```

## Review frontend

A Next.js app on `127.0.0.1:3000`. Reviewers sign in with their own token,
held in an httpOnly cookie, so every decision is attributed to them and not to
the app. It shows the queue of proposals the system
declined to decide alone, each with the query image beside every candidate's
best-matching sample, the scores, the margin, and the thresholds in force.

The reviewer's browser never talks to the API. Images and the review write go
through a **runtime proxy with an allowlist** (`src/app/api/v1/[...path]`),
so the API needs no public exposure, no CORS, and enrolment and identification
are not reachable from a browser at all. Scores are rendered as the raw
similarities they are — never as percentages, which would invite reading them
as probabilities.

```bash
cd frontend && npm install && npm run dev
```

Checks:

```bash
cd frontend && npm run typecheck && npm run lint && npm test
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
| GET | `/api/v1/me` | Who the presented credential belongs to. Any valid token. |
| POST | `/api/v1/enrolments` | Enrol a face sample. Idempotent; 202 on both first and repeat submissions. |
| GET | `/api/v1/face-samples/{uuid}` | Read a sample's processing state. |
| POST | `/api/v1/identifications` | Propose who a face belongs to. Audited. |
| GET | `/api/v1/identifications/{uuid}` | Read a past decision and its policy. |
| GET | `/api/v1/identifications` | List proposals awaiting review, oldest first. |
| POST | `/api/v1/identifications/{uuid}/review` | Record a human's conclusion. Audited. |
| DELETE | `/api/v1/persons/{uuid}` | Erase a person and their biometric material. Audited, `admin` scope. |
| GET | `/api/v1/identifications/{uuid}/image` | The submitted query image. |
| GET | `/api/v1/face-samples/{uuid}/image` | An enrolled sample image. |

Enrol a face:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/enrolments -H "Authorization: Bearer $TOKEN" -F source=crm -F external_id=42 -F image=@face.jpg
```

The response carries `person_uuid`, the sample, and `created` — `false` means
the submission repeated an earlier one and scheduled no further work.
Embedding happens in the `worker` service; poll the face-sample endpoint until
`processing_state` leaves `pending`.

Interactive docs are served at `/docs` in the `local` environment only.

## Authentication

Every endpoint except `/health`, `/readyz` and `/sessions` requires a bearer
token with the right scope. There are two ways to get one.

**People sign in with an email and password.** Accounts are created by an
operator; the password is read from a prompt or stdin, never from argv:

```bash
docker compose exec -T api python -m app.users create --email you@example.com --scope review
```

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d '{"email":"you@example.com","password":"..."}'
```

That returns a short-lived scoped credential — the same kind of credential as
below, so scopes, auditing and revocation behave identically. The web UI does
this for you and keeps the result in an httpOnly cookie. Changing a password or
disabling an account revokes every session it produced.

**Services use issued tokens**, out of band — there is no self-service
registration, because an API that can mint its own credentials can escalate its
own privileges:

```bash
docker compose exec api python -m app.tokens issue --subject alice@example.com --kind user --scope review
```

The secret is printed once and stored only as a SHA-256; it is not recoverable.
`list` and `revoke <token-uuid>` manage them, and revocation takes effect on the
next request.

Credentials expire after `FACEID_TOKEN_LIFETIME_DAYS` (default 90) unless
`--expires-in-days` overrides it. `--never-expires` is available but must be
asked for explicitly, and warns: such a credential stays valid until somebody
notices it has leaked. An expired credential is refused exactly like a revoked
one.

## Erasure

```bash
curl -sS -X DELETE "http://127.0.0.1:8000/api/v1/persons/$PERSON?reason=subject+request" -H "Authorization: Bearer $ADMIN_TOKEN"
```

Removes the person's metadata, their embeddings across **every** model
provenance, and their stored images. An image shared with another person's
sample is kept, so `images_removed` can be lower than `samples_removed`. The
audit record names who erased them and survives the deletion.

Two reconciliation sweeps run hourly in the worker: one removes vectors whose
person no longer exists, the other removes stored images that no face sample
and no identification references. Both cover leftovers from before erasure
existed, or from a partial failure. The image sweep ignores anything written in
the last hour, because enrolment stores an image before the row that names it.

## Rate limiting

`enrol` and `identify` are limited per credential per window — a stolen token
is the thing worth throttling, and callers behind one gateway should not
throttle each other. A refusal is a structured **429** carrying `Retry-After`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `FACEID_RATE_LIMIT_WINDOW_SECONDS` | `60` | Window length |
| `FACEID_RATE_LIMIT_IDENTIFY` | `30` | Identifications per credential per window |
| `FACEID_RATE_LIMIT_ENROL` | `120` | Enrolments per credential per window |

| Scope | Grants |
| --- | --- |
| `enrol` | submit enrolments, read sample state |
| `identify` | submit identifications |
| `review` | read the queue, read decisions and images, record reviews |
| `admin` | reserved for administrative actions |

Reviews are attributed to the authenticated principal. A `reviewer` name in the
request body is ignored — the audit log records people, not claims.

## Retention

Submitted identification images expire after
`FACEID_QUERY_IMAGE_RETENTION_DAYS` (default 30), swept hourly by the worker.
The identification record and its content hash outlive the image, so decisions
stay auditable after the biometric material is gone, and every purge is
audited. An image shared with an enrolled sample is never purged this way.

## Identity decisions

Thresholds have **no defaults in code** — the service refuses to start without
them, because a default would be a hard-coded matching threshold that silently
becomes production:

| Setting | Meaning |
| --- | --- |
| `FACEID_DECISION_ACCEPT_THRESHOLD` | At or above this similarity, propose a match |
| `FACEID_DECISION_REVIEW_THRESHOLD` | At or above this, ask a human |
| `FACEID_DECISION_POLICY_VERSION` | Recorded with every decision |
| `FACEID_DECISION_CANDIDATE_LIMIT` | Neighbours fetched per identification (default 10) |

The values in `.env.example` are illustrative starting points, **not validated
operating values** — tune them against your own population first.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/identifications -H "Authorization: Bearer $TOKEN" -F image=@face.jpg
```

The response carries `outcome` (`accept` / `review` / `reject`), the candidates
with raw cosine `score`s, the `margin` to the runner-up, and the thresholds
that produced it. Scores are uncalibrated similarities — **not probabilities**,
and `accept` is a proposal to act, not a determination of identity.

Only proposals with outcome `review` can be reviewed, and only once:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/identifications/$ID/review -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"outcome":"confirmed","note":"same person"}'
```

Every identification and review is written to an append-only audit log with the
actor, the policy in force, and the scores involved.

## Data model

| Table | Holds |
| --- | --- |
| `persons` | `person_uuid`, the sole internal key |
| `person_external_identifiers` | upstream `id` / `local_id`, unique per `(source, kind, value)` |
| `face_samples` | many captures per person, deduplicated per person by content hash, with processing state |
| `face_embeddings` | what was embedded, under which model provenance, into which collection |
| `identifications` | each attempt, its decision, and the thresholds in force at the time |
| `audit_events` | append-only record of decisions, reviews and purges; no foreign keys, so it outlives what it describes |
| `api_tokens` | credentials, stored only as SHA-256 hashes, with scopes |
