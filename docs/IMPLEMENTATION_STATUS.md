# Implementation Status

Authoritative record of what is built. One phase at a time; a phase is only
marked complete when its acceptance criteria have been verified by running
them.

Last updated: 2026-08-18.

## Phase 0 — Foundation & Contracts — ✅ COMPLETE

Repository skeleton, configuration, error envelope, structured logging,
readiness registry, architectural seams, container topology, and the
lint/type/test toolchain.

### Delivered

| Item | Location |
| --- | --- |
| Environment-driven settings, required DSNs, no in-code credentials | `backend/app/core/config.py` |
| Structured error envelope + handlers (domain, validation, HTTP) | `backend/app/core/errors.py` |
| JSON logging with biometric-field stripping | `backend/app/core/logging.py` |
| Readiness probe registry | `backend/app/core/readiness.py` |
| `GET /api/v1/health`, `GET /api/v1/readyz` with schemas | `backend/app/api/v1/health.py` |
| Application factory | `backend/app/main.py` |
| `ModelAdapter` seam (name/version/preprocessing_version/warmup) | `backend/app/adapters/base.py` |
| `StorageConnector` seam (provider/ping) | `backend/app/connectors/base.py` |
| Postgres + Redis + Qdrant topology, Qdrant unpublished | `docker-compose.yml` |
| ruff + mypy(strict) + pytest configuration | `backend/pyproject.toml` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Service starts and answers liveness | `docker compose up`, `curl /api/v1/health` | ✅ `{"status":"ok",...}` HTTP 200 |
| Readiness reflects real probe state, no false "healthy" | unit tests + live `curl /api/v1/readyz` | ✅ `{"status":"ready","checks":[]}`; failing probe → 503 |
| Missing dependency configuration fails startup | `tests/test_config.py` | ✅ ValidationError names `postgres_dsn`, `redis_dsn` |
| All errors use the structured envelope | `tests/test_errors.py` + live 404 | ✅ envelope on 404/422/503 |
| Biometric material cannot reach logs | `tests/test_logging.py` | ✅ all `SENSITIVE_KEYS` stripped |
| Seams free of HTTP coupling | `tests/test_seams.py` | ✅ no framework imports in adapters/connectors |
| Qdrant not reachable from the host | `curl 127.0.0.1:6333` vs. in-container fetch | ✅ refused from host, 200 inside network |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 20 passed, 0 lint errors, 0 type errors |

### Deliberately NOT in Phase 0

No domain model, no database schema or migrations, no Qdrant collections, no
SCRFD, no AdaFace, no embeddings, no enrolment, no matching, no thresholds, no
audit log, no frontend. `app/domain/` is intentionally empty.

## Phase 1 — Person & Face Domain + Persistence — ✅ COMPLETE

Domain entities, repository interfaces, the PostgreSQL schema and its
migration, the Postgres connector, and its registration as a readiness probe.

### Delivered

| Item | Location |
| --- | --- |
| `Person` keyed by `person_uuid`; `ExternalIdentifier` (source-scoped); `FaceSample` | `backend/app/domain/models.py` |
| `PersonRepository` / `FaceSampleRepository` protocols, `ConflictError` | `backend/app/domain/repositories.py` |
| Tables with uniqueness and check constraints | `backend/app/connectors/postgres/tables.py` |
| Engine, transactional sessions, `ping()` | `backend/app/connectors/postgres/connector.py` |
| SQLAlchemy Core repository implementations | `backend/app/connectors/postgres/repositories.py` |
| Alembic setup + initial revision `5e5780ed997c` | `backend/migrations/` |
| Connector opened at startup and registered as a readiness probe | `backend/app/main.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| `person_uuid` is the only internal key; external ids are attributes | schema inspection + `tests/test_postgres_repositories.py` | ✅ no FK or PK references an external id |
| External identifiers are source-scoped | integration test: same value from two sources | ✅ resolves to two different people |
| `id` and `local_id` are distinct namespaces | integration test | ✅ lookup by the other kind returns None |
| One identifier cannot denote two people in a source | integration test | ✅ raises `ConflictError` |
| Re-linking an identifier to the same person is idempotent | integration test, linked 3× | ✅ exactly one row |
| A person supports many face samples | integration test, 3 samples | ✅ all retained |
| The same image twice for one person is rejected | integration test | ✅ `ConflictError` |
| The same image may belong to two people | integration test | ✅ both stored |
| A sample for an unknown person is rejected | integration test | ✅ `ConflictError` |
| Deleting a person removes their samples | integration test | ✅ cascade confirmed |
| Migration applies to a real database | `alembic upgrade head`, `\d+ face_samples` | ✅ revision `5e5780ed997c` at head |
| Migration runs from inside the image | `docker compose exec api alembic current` | ✅ `5e5780ed997c (head)` |
| Readiness reflects the real database | live `curl /api/v1/readyz` | ✅ `postgres` healthy; 503 when stopped; recovers on restart |
| Domain has no ORM or HTTP dependency | `tests/test_seams.py`, plain dataclasses | ✅ |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 52 passed with a database, 35 passed + 17 skipped without |

### Deliberately NOT in Phase 1

No HTTP endpoints over persistence (nothing is externally visible yet), no
embeddings or embedding-provenance rows, no Qdrant collections, no detection,
no recognition, no enrolment flow, no thresholds, no audit log, no frontend.

### Notes

- Postgres is now published on `127.0.0.1:5432` so migrations and integration
  tests can run from the host. This is a local-development convenience and is
  documented as such in `docker-compose.yml`. Qdrant remains unpublished.
- Integration tests are skipped unless `FACEID_TEST_POSTGRES_DSN` is set, so
  they cannot pass silently without a database.

## Phase 2 — Detection (SCRFD adapter) — ✅ COMPLETE

Real SCRFD inference through onnxruntime, checksum-verified weights, pure
preprocessing primitives, and canonical face alignment.

### Delivered

| Item | Location |
| --- | --- |
| `BoundingBox`, `FaceLandmarks`, `DetectedFace`, `AlignedFace` | `backend/app/domain/detection.py` |
| Letterbox, blob, anchors, decoding, NMS, Umeyama alignment | `backend/app/adapters/preprocessing.py` |
| `SCRFDDetector`, `SCRFDConfig`, checksum verification | `backend/app/adapters/scrfd.py` |
| Settings → detector construction | `backend/app/adapters/factory.py` |
| Detection settings and thresholds | `backend/app/core/config.py` |
| Read-only weights mount | `docker-compose.yml` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Real inference, nothing faked | detection on a real photograph (`skimage.data.astronaut`) | ✅ 1 face, score 0.83 |
| Landmarks are anatomically coherent | ordering assertions on eyes/nose/mouth | ✅ |
| Landmarks fall inside the detected box | integration test | ✅ |
| Detection is deterministic | repeated runs compared | ✅ identical scores and points |
| Multiple faces are all returned | two faces in one canvas | ✅ 2 detections, confidence-ordered |
| Coordinates survive image rescaling | detect at 1x and 2x | ✅ agree within 6 px |
| Blank image yields no faces | integration test | ✅ `[]` |
| Thresholds are configuration, not constants | settings-driven config test; strict vs lenient | ✅ 0.999 → no faces |
| Weights verified before loading | wrong checksum, locally and in the container | ✅ `ModelIntegrityError` |
| `model_version` derives from the weight bytes | compared against `file_sha256` | ✅ |
| Missing checksum warns rather than passing silently | `caplog` assertion | ✅ |
| Provenance triple is reported | adapter properties, in-container check | ✅ name / version / preprocessing_version |
| Alignment produces the recognition geometry | crop shape, dtype, template fit | ✅ 112×112 uint8 |
| Alignment normalises in-plane rotation | 20° rotated input vs upright | ✅ mean abs difference < 25 |
| Adapter satisfies `ModelAdapter` | `isinstance` protocol check | ✅ |
| Adapter free of HTTP coupling | `tests/test_seams.py` | ✅ |
| Runs inside the container image | `docker compose exec` with mounted weights | ✅ loads and detects |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 119 passed; 81 passed + 38 skipped without weights or database |

### Deliberately NOT in Phase 2

No recognition or embeddings, no Qdrant, no HTTP endpoint for detection, no
enrolment, no identity decisions or matching thresholds, no frontend. The
detector is not loaded by the API process — that belongs with the worker that
will use it.

### Notes

- Weights (`scrfd_10g_bnkps.onnx`, SHA-256 `5838f7fe…5b91`) are git-ignored via
  `models/` and mounted read-only into the container.
- `scikit-image` is a dev dependency only: it supplies a real photograph of a
  face offline, so detection tests need no network and no committed image.
## Phase 3 — Recognition (AdaFace adapter) + embedding provenance — ⬜ NOT STARTED

Planned: the AdaFace `ModelAdapter` consuming the aligned crops Phase 2
produces, emitting embeddings tagged with `model_name`, `model_version` and
`preprocessing_version`. Similarity scoring only — no thresholds, no
match/no-match verdicts, no conversion of similarity into probability.
## Phase 4 — Qdrant connector + vector storage — ⬜ NOT STARTED
## Phase 5 — Idempotent enrolment + Redis jobs — ⬜ NOT STARTED
## Phase 6 — Identity decision layer, configurable thresholds, audit log — ⬜ NOT STARTED
## Phase 7 — Next.js + TypeScript review frontend — ⬜ NOT STARTED

## Known issues

- `docker compose build` cannot reach PyPI in this sandbox (DNS is unavailable
  to the default bridge network at build time). The image builds with
  `docker build --network=host -t faceid-api:dev ./backend`, and the compose
  file pins `image: faceid-api:dev` so `docker compose up -d --no-build` uses
  it. This is an environment limitation, not a defect in the Dockerfile; on a
  normal host `docker compose up --build` works unchanged.
