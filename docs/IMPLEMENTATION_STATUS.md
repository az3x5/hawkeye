# EagleEye — implementation status

Authoritative record of what is built. One phase at a time; a phase is only
marked complete when its acceptance criteria have been verified by running
them.

Last updated: 2026-08-29.

Historical phases below record the verified face-identity build. The newer
multimodal roadmap is maintained in `MASTER_IMPLEMENTATION_PLAN.md`, and the
evidence baseline is `REPOSITORY_AUDIT.md`. Legacy Hawkeye/`FACEID_` identifiers
remain compatibility names, not the name for new product-facing work.

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
## Phase 3 — Recognition (AdaFace adapter) + embedding provenance — ✅ COMPLETE

Real AdaFace IR-101 inference over the aligned crops Phase 2 produces, with
full embedding provenance and similarity scoring that stops short of any
verdict.

### Delivered

| Item | Location |
| --- | --- |
| `EmbeddingProvenance`, `FaceEmbedding`, `IncomparableEmbeddingsError` | `backend/app/domain/recognition.py` |
| IR-101 backbone, re-implemented from the published architecture | `backend/app/adapters/iresnet.py` |
| `AdaFaceRecognizer`, `AdaFaceConfig`, `cosine_similarity` | `backend/app/adapters/adaface.py` |
| Settings → recogniser construction | `backend/app/adapters/factory.py` |
| Recognition settings (path, checksum, device, batch size) | `backend/app/core/config.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Real inference, nothing faked | embeddings over the detect→align→embed pipeline | ✅ |
| Backbone matches the published checkpoint | `load_state_dict(strict=True)` on 917 tensors | ✅ loads with no missing or unexpected keys |
| Parameter count is plausible for IR-101 | structural test | ✅ ~65M, inside the 60–70M window |
| Embeddings are 512-d and L2-normalised | shape and norm assertions | ✅ norm 1.0 |
| A face matches itself | cosine similarity | ✅ 1.000 |
| Identity survives a 15° rotation | rotate, re-detect, re-align, re-embed | ✅ 0.992 |
| Identity survives a brightness change | α=0.7 | ✅ 0.997 |
| A mirrored face still matches | horizontal flip | ✅ 0.973 |
| Unrelated input scores near zero | face vs. random noise | ✅ −0.017 |
| Genuine and impostor scores are well separated | difference assertion | ✅ > 0.5 apart |
| Embedding is deterministic | repeated runs | ✅ identical vectors |
| Batching matches one-at-a-time | batch_size=2 vs. individual | ✅ agree to 1e-4 |
| Batch order is preserved | per-index comparison | ✅ |
| Every embedding carries the full triple | provenance assertions | ✅ name / version / preprocessing_version |
| `model_version` derives from the weight bytes | compared against `file_sha256` | ✅ |
| Weights verified before loading | wrong checksum | ✅ `ModelIntegrityError` |
| Crops from other preprocessing are refused | stale `AlignedFace` | ✅ `RecognitionError` |
| Embeddings from different provenance never compared | mismatched `model_version` | ✅ `IncomparableEmbeddingsError` |
| Similarity is not a probability | negative scores are reachable and returned | ✅ |
| No decision surface exists | absence of `verify` / `identify` / `is_match`; no threshold on the config | ✅ |
| Input validated before weights are loaded | validation tests pass without weights present | ✅ |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 170 passed; 115 passed + 55 skipped without weights or database |

### Deliberately NOT in Phase 3

No vector storage (embeddings live only in memory until Qdrant lands in Phase
4), no enrolment, no HTTP endpoint, no thresholds, no matching, no identity
decisions, no audit log, no frontend.

### Notes

- Weights: `adaface_ir101_webface12m.safetensors`, SHA-256 `2ea535a4…bf56`,
  from the AdaFace author's own HuggingFace repository. Git-ignored via
  `models/`, mounted read-only into the container.
- The backbone is re-implemented rather than imported from the weights
  repository, so no third-party code is executed to load a checkpoint.
- `Flatten` uses `reshape`, not `view`: the preceding block can leave a
  non-contiguous tensor, which `view` rejects.
- Recognition **is** now verified inside the container image. The torch install
  during `docker build` failed repeatedly with read timeouts in this sandbox
  and only succeeded on a later retry; the resulting image reports
  `adaface_ir101_webface12m`, model version `2ea535a43877…`, dimension 512,
  unit-norm embeddings, self-similarity 1.0, and a preprocessing version shared
  with the detector. The earlier "not yet verified in the container" note is
  superseded.
- torch pushes the image well past its previous 500MB. Since recognition is
  meant to run in a worker rather than the API process, splitting the image is
  worth doing before this grows further.
## Phase 4 — Qdrant connector + vector storage — ✅ COMPLETE

Vector persistence and nearest-neighbour search, with provenance isolation
enforced structurally rather than by convention.

### Delivered

| Item | Location |
| --- | --- |
| `StoredEmbedding`, `VectorMatch`, `VectorRepository` protocol | `backend/app/domain/vectors.py` |
| Collection naming derived from the provenance triple | `backend/app/connectors/qdrant/naming.py` |
| `QdrantConnector` (client lifecycle, `ping`) | `backend/app/connectors/qdrant/connector.py` |
| `QdrantVectorRepository` (upsert, search, get, delete) | `backend/app/connectors/qdrant/repository.py` |
| `face_embeddings` metadata table + revision `b8265d112b06` | `backend/app/connectors/postgres/tables.py`, `backend/migrations/` |
| Qdrant registered as a readiness probe | `backend/app/main.py` |
| Loopback overlay for local integration tests | `docker-compose.dev.yml` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Embeddings round-trip through the store | integration test | ✅ vector recovered within 1e-6 |
| Re-writing a sample replaces, never duplicates | integration test | ✅ one point, new vector |
| A person may hold many embeddings | 3 samples, one person | ✅ all returned |
| Search orders by similarity, best first | integration test | ✅ self-match ≈ 1.0, descending |
| Scores are similarities, not probabilities | search with an inverted vector | ✅ negative score returned |
| Search never crosses provenance | identical vector under a second model version | ✅ no results |
| Any provenance change maps to a new collection | parametrised over all three fields | ✅ |
| Mixed-provenance writes are refused | integration test | ✅ `ValueError` |
| A person can be excluded from a search | integration test | ✅ only other people returned |
| Deleting a person removes only their vectors | integration test | ✅ count returned, others intact |
| Absent collections are handled, not crashed | search/get/delete before creation | ✅ empty, None, 0 |
| Payload carries identifiers and provenance only | exact key-set assertion | ✅ no images or free text |
| Qdrant satisfies `StorageConnector` | protocol check + `ping` | ✅ |
| Readiness covers both stores | app startup registers postgres and qdrant | ✅ |
| Base topology still publishes no Qdrant port | `docker compose config` on the base file | ✅ `ports: NONE` |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 197 passed; 115 passed + 82 skipped with nothing available |

### Deliberately NOT in Phase 4

No enrolment flow, no HTTP endpoints, no identity decisions, no matching
thresholds, no audit log, no frontend. `search()` deliberately has no score
cut-off.

### Notes

- `qdrant-client` was missing from `pyproject.toml` at first and the container
  crashed on import. Declared now, but the image has **not been rebuilt since**,
  so Phase 4 is verified on the host and *not* yet inside the container image.
- The client's background version handshake raises on unreachable servers and
  turned a clean connection error into an unhandled thread exception under
  `filterwarnings = ["error"]`; it is disabled, with readiness reported by
  `ping()` instead.

Planned: the Qdrant `StorageConnector`, collections keyed by `person_uuid` plus
sample id, embedding-provenance rows in PostgreSQL, and a guarantee that
vectors from different provenance triples never share an index. Qdrant stays
unpublished.
## Phase 5 — Idempotent enrolment + Redis jobs — ✅ COMPLETE

The first externally visible surface. Enrolment records a face sample and
schedules embedding; a worker runs the models and stores the vector.

### Delivered

| Item | Location |
| --- | --- |
| `EmbeddingJob`, `ProcessingState`, `JobQueue` and `ObjectStore` protocols | `backend/app/domain/jobs.py` |
| Content-addressed image storage | `backend/app/connectors/filesystem/` |
| Redis connector and reliable-queue implementation | `backend/app/connectors/redis/` |
| Idempotent enrolment, `SampleReader` | `backend/app/services/enrolment.py` |
| `POST /enrolments`, `GET /face-samples/{uuid}` | `backend/app/api/v1/enrolments.py` |
| Request-scoped dependency wiring | `backend/app/api/v1/dependencies.py` |
| Embedding worker process | `backend/app/worker.py` |
| Processing state + revision `dcece6ee12f6` | `backend/app/connectors/postgres/tables.py`, `backend/migrations/` |
| `worker` service, shared object volume | `docker-compose.yml` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| The same submission twice converges | service and API tests | ✅ same person and sample uuid, `created: false` |
| A repeat schedules no further work | queue depth asserted | ✅ exactly one job |
| Repeating many times stays stable | 5 repeats | ✅ same sample throughout |
| A second image adds a sample to the same person | service test | ✅ two samples, one person |
| A concurrent duplicate converges rather than failing | insert race simulated | ✅ no second row, no second job |
| Identifiers are scoped by source | same value, two sources | ✅ two people |
| Identifiers denoting two people are refused | service and API tests | ✅ 409 `conflicting_identifiers` |
| At least one identifier is required | service and API tests | ✅ 422 `invalid_enrolment` |
| Uploads are validated | type, empty, oversized, missing | ✅ structured 422 with `field` |
| Every endpoint has schemas and errors | OpenAPI assertions | ✅ 202/404/409/422 documented |
| No image bytes in responses or job payloads | explicit assertions | ✅ identifiers and hash only |
| A real face is embedded end to end | worker against real Postgres, Redis, Qdrant and both models | ✅ `processed`, 512-d vector stored |
| The stored vector is findable | search by the stored embedding | ✅ own sample at ≈1.0 |
| A faceless image fails cleanly | blank frame | ✅ `failed`, "no face was detected", no vector |
| A missing object fails the job | object deleted before processing | ✅ `failed`, reason recorded |
| An undecodable image fails the job | corrupt bytes | ✅ `failed`, reason recorded |
| Multiple faces are refused, not guessed | two faces in one image | ✅ `failed`, "requires exactly one" |
| Replaying a job does not duplicate the vector | same job processed twice | ✅ one point |
| A reserved job is held until reported | in-flight list inspected | ✅ survives a dead worker |
| Failed jobs are kept with their reason | failure list inspected | ✅ reason retained |
| A corrupt queue entry is reported | malformed JSON pushed | ✅ `JobQueueError`, not skipped |
| Worker takes no inbound port | `docker compose config` | ✅ `ports: none` |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 265 passed |

### Deliberately NOT in Phase 5

No identity matching, no thresholds, no review or merge, no audit log, no
frontend. Search exists in the vector layer but nothing calls it to decide who
someone is.

### Notes

- `python-multipart` and `redis` were missing from `pyproject.toml` and were
  added once the tests and the container caught it.
- Enrolment deliberately answers 202 for repeats rather than 409: callers
  retrying after a timeout need convergence, not an error.
- Images live in a filesystem object store behind the `ObjectStore` interface.
  Swapping it for S3 or GCS is a connector change and nothing else.
- Two bugs the container caught that the host tests did not:
  - The object volume was root-owned while the process runs as uid 10001. The
    readiness probe reported it correctly; the image now creates
    `/srv/objects` owned by the runtime user so Docker seeds the volume with
    that ownership.
  - `reserve()` raced redis-py's read deadline. redis-py derives a blocking
    command's deadline from the command's own timeout, so a 5s block on an
    empty queue raised instead of returning "no job". The host tests used a 1s
    block and passed by luck. The connector now sets a socket timeout with
    headroom, `reserve()` refuses a block that would exceed it, and two
    regression tests cover both halves.
- Integration tests must use a separate Redis database (15) from the running
  worker, which otherwise consumes the tests' jobs off database 0.
## Phase 6 — Identity decision layer, configurable thresholds, audit log — ✅ COMPLETE

The layer that turns similarity into action, the policy that governs it, and
the record that makes both accountable.

### Delivered

| Item | Location |
| --- | --- |
| Pure decision logic: thresholds, aggregation, bands, margin | `backend/app/domain/identity.py` |
| Audit types, typed actors, append-only `AuditLog` protocol | `backend/app/domain/audit.py` |
| Append-only log and identification store | `backend/app/connectors/postgres/audit.py` |
| Identification and review orchestration | `backend/app/services/identification.py` |
| `POST /identifications`, `GET /identifications/{uuid}`, `POST .../review` | `backend/app/api/v1/identifications.py` |
| Required threshold settings | `backend/app/core/config.py` |
| `identifications`, `audit_events` + revision `35141621313d` | `backend/app/connectors/postgres/tables.py`, `backend/migrations/` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Thresholds have no default in code | settings are required fields; every test and service must state a policy | ✅ startup fails without them |
| The same evidence decides differently under another policy | unit test, and live by restarting under a stricter policy | ✅ 0.9804 → `accept` at 0.62, `review` at 0.99 |
| Bands are closed at the bottom | boundary tests at and just below each threshold | ✅ |
| `accept_at` must exceed `review_at` | construction test | ✅ refuses "no band" policies |
| A person is scored by their best sample | aggregation tests | ✅ a weak sample does not drag them down |
| Candidate ordering is deterministic under ties | repeated runs | ✅ |
| Scores are not probabilities | negative score representable; no normalisation; schema says so | ✅ |
| Margin is surfaced, not acted on | narrow-margin test | ✅ outcome unchanged |
| Every identification is audited | live `audit_events` inspection | ✅ `identification_performed`, actor `system` |
| Every review is audited | live inspection | ✅ `identification_reviewed`, actor `user` |
| Automatic proposals are never attributed to a person | actor kind asserted | ✅ |
| Only `review` outcomes can be reviewed | unit, API and live | ✅ 409 `review_not_permitted` |
| A review cannot be overwritten | store and API tests, live | ✅ 409 on second review |
| The log exposes no update or delete | public surface asserted | ✅ `{record, for_person, for_identification}` |
| Audit records outlive what they describe | person deleted, event remains | ✅ no foreign keys |
| Thresholds are stored with the decision | column inspection | ✅ readable after policy change |
| Every endpoint has schemas, validation, structured errors | OpenAPI and error-envelope tests | ✅ 201/404/409/422 |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 336 passed; 259 passed + 76 skipped without dependencies |

### Deliberately NOT in Phase 6

No merge or split of people, no bulk review queue endpoint, no frontend. The
decision layer proposes; acting on an `accept` beyond recording it is left to
the caller.

### Notes

- **A bug found only by running it.** The review endpoint recorded and audited
  the review correctly but answered with nulls: it read the record back through
  a *second* database session that could not see the first one's uncommitted
  write. The service now returns the updated record directly, and a regression
  test covers it. Unit tests with fakes had passed, because fakes share no
  transaction semantics with a database.
- Introducing `IdentificationStore` as a domain protocol replaced several
  `object` annotations that had been hiding exactly this class of error from
  the type checker.
- Identification loads the models in the API process, unlike enrolment: the
  caller is waiting for an answer, so the work cannot be handed to the worker.
## Phase 7 — Next.js + TypeScript review frontend — ✅ COMPLETE

A review queue over the existing endpoints, plus the backend additions it
needed to be usable at all.

### Delivered

| Item | Location |
| --- | --- |
| Review queue listing (`GET /identifications`) | `backend/app/api/v1/identifications.py` |
| Query image retention at identification time | `backend/app/services/identification.py` |
| Image endpoints for query and enrolled samples | `backend/app/api/v1/{identifications,enrolments}.py` |
| `awaiting_review` on the store and its protocol | `backend/app/{domain/identity.py,connectors/postgres/audit.py}` |
| Review queue page | `frontend/src/app/page.tsx` |
| Review detail page and form | `frontend/src/app/review/[id]/` |
| Runtime API proxy with an allowlist | `frontend/src/app/api/v1/[...path]/route.ts` |
| Typed API client, formatting helpers | `frontend/src/lib/` |
| Frontend image and compose service | `frontend/Dockerfile`, `docker-compose.yml` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| The queue lists only proposals awaiting a human | API tests over accept/reject/reviewed | ✅ 1 of 4 |
| The backlog is oldest first | API test | ✅ |
| Each entry carries score, margin, candidate count, policy | API test and live page | ✅ |
| An invalid limit fails validation | parametrised | ✅ 422 |
| The submitted query image is retained and served | live through the frontend | ✅ 200 image/jpeg |
| Enrolled sample images are served | live | ✅ 200 image/jpeg |
| Images are not reachable by content hash alone | API test | ✅ 422/404 |
| Biometric images are not shared-cacheable | header assertions, API and proxy | ✅ `private, no-store` |
| The proxy forwards only what a reviewer needs | unit tests and live | ✅ `/enrolments` → 404 `not_proxied` |
| A read-only path refuses a write | unit test | ✅ |
| Scores never render as percentages | unit tests on the formatter | ✅ raw similarities, negatives preserved |
| A narrow margin is flagged, not acted on | unit test and page copy | ✅ |
| The frontend carries no decision logic | no thresholds or aggregation in `frontend/src` | ✅ presents API output only |
| Reviews require an attributed reviewer | form validation | ✅ submit disabled until named |
| An automatic decision shows no review controls | detail page branch | ✅ explains why instead |
| A recorded review is shown as final | detail page branch, live | ✅ "cannot be changed" |
| Full loop works in containers | queue → detail → confirm → audit | ✅ both audit rows with correct actor kinds |
| Frontend typecheck, lint, tests, build | tsc --noEmit, eslint, vitest, next build | ✅ 17 tests, 0 errors |
| Backend lint, types, tests | ruff, mypy --strict, pytest | ✅ 353 passed |

### Deliberately NOT in Phase 7

No authentication — **fixed in Phase 8**; at the time of Phase 7 the reviewer
typed their own name and nothing verified it. No merge or split UI, no person browser, no enrolment UI,
no pagination beyond a limit.

### Notes

- Two container-only bugs, both caught by running it: Next bound to localhost
  inside the container and was unreachable, and the `rewrites()` proxy baked
  the build-time fallback address into the build output. The second was
  replaced with a runtime route handler, which is also narrower than the
  blanket passthrough it replaced.
- The frontend needed three backend additions to be usable, so they are part
  of this phase rather than a phase of their own: the queue listing, query
  image retention, and the two image endpoints.

## Known issues

- `docker compose build` cannot reach PyPI in this sandbox (DNS is unavailable
  to the default bridge network at build time). The image builds with
  `docker build --network=host -t faceid-api:dev ./backend`, and the compose
  file pins `image: faceid-api:dev` so `docker compose up -d --no-build` uses
  it. This is an environment limitation, not a defect in the Dockerfile; on a
  normal host `docker compose up --build` works unchanged.

## Phase 8 — Authentication, authorisation, retention — ✅ COMPLETE

Closes the correctness gap Phase 7 left open: the audit log recorded a
self-declared name, so it evidenced claims rather than people. Also settles the
retention question that query-image storage opened.

### Delivered

| Item | Location |
| --- | --- |
| Principals, scopes, token minting and hashing | `backend/app/domain/auth.py` |
| Credential storage (hashes only) | `backend/app/connectors/postgres/tokens.py` |
| Bearer authentication and scope dependency | `backend/app/api/v1/security.py` |
| Scope enforcement on every endpoint | `backend/app/api/v1/{enrolments,identifications}.py` |
| Out-of-band token administration | `backend/app/tokens.py` |
| Query-image retention sweep | `backend/app/retention.py`, `backend/app/worker.py` |
| `api_tokens` + revision `a1e093fc7216` | `backend/app/connectors/postgres/tables.py`, `backend/migrations/` |
| Reviewer sign-in, session cookie, token forwarding | `frontend/src/{lib/session.ts,app/sign-in,app/sign-out}` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Every protected endpoint refuses an anonymous caller | live, no token | ✅ 401 `not_authenticated` |
| An unknown or revoked token is refused | live and unit | ✅ 401, indistinguishable from each other |
| Scopes are enforced, not merely recorded | live cross-checks | ✅ ingest→enrol 202, ingest→queue 403, reviewer→queue 200, reviewer→enrol 403 |
| Health and readiness stay open | live | ✅ 200 without a token |
| The reviewer is the authenticated principal | live review with `"reviewer":"mallory"` in the body | ✅ recorded as `alice@example.com` |
| A service credential is never recorded as a person | audit inspection | ✅ `ingest-service` logged as `system` |
| The secret is never stored | table inspection in tests | ✅ only its SHA-256 |
| Revocation takes effect immediately | revoke then call | ✅ 401 on the next request |
| An unknown scope name cannot lock anyone out | scope array corrupted in a test | ✅ unknown entries dropped |
| Reviewers sign in with their own token | real browser: sign-in → queue → confirm | ✅ attributed to `alice@example.com` |
| An unauthenticated visit redirects to sign-in | real browser | ✅ 307 → `/sign-in` |
| Expired query images are purged | retention tests | ✅ removed after the cutoff |
| Recent images are kept | retention test | ✅ |
| The identification record outlives its image | retention test | ✅ hash retained |
| An enrolled sample is never purged as a query | shared-hash test | ✅ kept |
| Every purge is audited | audit inspection | ✅ `query_image_purged`, system actor |
| The sweep cannot stall the worker | failure path logged and swallowed | ✅ no failures live |
| Lint, types and tests clean | ruff, mypy --strict, pytest, tsc, eslint, vitest | ✅ 383 backend, 19 frontend |

### Deliberately NOT in Phase 8

No token expiry, no rate limiting (both added in Phase 9), no per-person access
control — any `review` holder can see any identification. No merge/split, still.

### Notes

- The `reviewer` request field was **removed, not deprecated**. A test now
  asserts that a name supplied in the body is ignored.
- Vitest needed its own alias config to resolve `@/` the way the app does;
  without it the proxy tests silently could not import the route.

## Phase 9 — Token lifetime and rate limiting — ✅ COMPLETE

Narrows the two attack surfaces Phase 8 left open: a leaked credential was
valid until somebody noticed, and nothing stopped one probing the gallery at
speed.

### Delivered

| Item | Location |
| --- | --- |
| Credential expiry, `usable()`, lifetime at issue | `backend/app/domain/auth.py` |
| `expires_at` storage + revision `6904bc245c44` | `backend/app/connectors/postgres/{tables,tokens}.py` |
| `--expires-in-days` / `--never-expires`, expiry in `list` | `backend/app/tokens.py` |
| Fixed-window limiter | `backend/app/connectors/redis/rate_limit.py` |
| `rate_limited` dependency, 429 with `Retry-After` | `backend/app/api/v1/security.py`, `backend/app/core/errors.py` |
| Identification records outlive their people + revision `cde2c7d24322` | `backend/app/connectors/postgres/tables.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| A lifetime is applied at issue | live `issue` | ✅ 90-day default shown |
| An expired credential is refused | backdated expiry, live | ✅ 401, same message as revoked |
| `list` distinguishes active, revoked and expired | live | ✅ |
| A never-expiring credential must be asked for, and warns | live | ✅ explicit flag + warning |
| Requests within the limit are allowed | unit and live | ✅ 29 of 30 remaining after one |
| The next request is refused | live burst of 33 | ✅ 30 × 201 then 429 |
| A refusal says when to come back | live headers | ✅ `Retry-After: 46` |
| Callers and actions are counted separately | unit | ✅ |
| The window expires | unit, real Redis | ✅ |
| Hammering cannot extend the window | unit | ✅ retry-after never grows |
| Authorisation is checked before counting | unit | ✅ 403 consumes nothing |
| An unconfigured limiter fails open, visibly | unit | ✅ serves, logs a warning |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 400 passed |

### Notes

- **A 500 found by running it, not by testing it.** Identification crashed when
  the vector store still held a candidate the metadata store had forgotten —
  `identifications.best_person_uuid` had a foreign key to `persons`. An
  identification is a historical record and must outlive the person it named,
  exactly as `audit_events` does; the constraint is dropped and two regression
  tests cover it. Alembic does not autogenerate foreign-key removal, so that
  migration is hand-written.
- Two test-isolation defects surfaced in the same run and are fixed: the Qdrant
  suite created collections under the *real* model name and dropped them in
  teardown, deleting the collection the worker tests were using; and a worker
  assertion inferred point count from a similarity search, which is meaningless
  when identical photographs produce tied vectors.

### Known gaps after Phase 9

- **Orphaned vectors** — fixed in Phase 10.
- No per-person access control, no merge/split, thresholds still unvalidated,
  model weight provenance unresolved.

## Phase 10 — Erasure — ✅ COMPLETE

Closes the privacy hole Phase 9 named: a person deleted from the metadata store
kept a searchable face in the vector store.

### Delivered

| Item | Location |
| --- | --- |
| Cross-provenance deletion, collection and person enumeration | `backend/app/connectors/qdrant/repository.py` |
| `PersonEraser`, `reconcile_orphaned_vectors` | `backend/app/services/erasure.py` |
| `DELETE /persons/{uuid}` (`admin` scope) | `backend/app/api/v1/persons.py` |
| Hourly reconciliation in housekeeping | `backend/app/worker.py` |
| `person_erased`, `orphaned_vectors_purged` actions | `backend/app/domain/audit.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| The person is removed from the metadata store | integration test | ✅ |
| Their samples go with them | integration test | ✅ cascade |
| Their face is no longer searchable | vector fetched, erased, refetched | ✅ gone |
| Vectors from every model version are removed | two provenances | ✅ both, count 2 |
| Their stored image is deleted | integration test | ✅ |
| An image shared with another person is kept | two people, one photograph | ✅ `images_removed` 0, other person intact |
| Another person's vectors are untouched | integration test | ✅ |
| Erasure is audited and the record survives | audit read after deletion | ✅ actor, reason, counts; person row gone |
| Erasing an unknown or already-erased person is reported | integration and live | ✅ 404 `person_not_found` |
| Only `admin` may erase | live with an `enrol`/`identify` credential | ✅ 403 |
| Orphaned vectors are removed | integration and live | ✅ 25 real leftovers cleaned |
| A dry run reports without deleting | integration test | ✅ |
| Reconciliation is audited as a system action | integration test | ✅ |
| No orphans remain in the live system | dry run against live data | ✅ 0 |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 415 passed |

### Notes

- Reconciliation found and removed 25 genuinely orphaned vectors created by
  earlier phases' testing, which is the clearest evidence the hole was real.
- The reconciliation tests assert `>= 1` rather than an exact count: they scan
  every collection, so the total depends on what else the environment holds.
  The specific vector under test is still asserted gone.

### Known gaps after Phase 10

- Erasure is not transactional across the three stores; images left by a crash
  were not reconciled — **fixed in Phase 11**.
- No per-person access control; no merge/split; thresholds unvalidated; model
  weight provenance unresolved.

## Phase 11 — Orphaned image reconciliation — ✅ COMPLETE

Completes the erasure story symmetrically: vectors were reconciled, images were
not, so a crash mid-erase could leave a face on disk attached to nobody.

### Delivered

| Item | Location |
| --- | --- |
| `list_digests(older_than=...)` on the object store and its protocol | `backend/app/connectors/filesystem/object_store.py`, `backend/app/domain/jobs.py` |
| `reconcile_orphaned_images` with a grace period | `backend/app/services/erasure.py` |
| Third hourly sweep in housekeeping | `backend/app/worker.py` |
| `orphaned_image_purged` action | `backend/app/domain/audit.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| An unreferenced image is removed | integration test | ✅ |
| An enrolled image is kept | integration test | ✅ |
| An image a past identification used is kept | integration test | ✅ retention owns those |
| A recent image is left alone | grace period test | ✅ no race with live enrolment |
| A dry run reports without deleting | integration test | ✅ |
| The purge is audited as a system action | audit assertion | ✅ hash and reason recorded |
| A nonsensical grace period is refused | parametrised | ✅ |
| Vector and image sweeps together close a partial erasure | end-to-end test | ✅ nothing left behind |
| Housekeeping runs in the worker without failures | live logs | ✅ purged on first sweep |
| Nothing referenced was deleted | live audit of disk against rows | ✅ 0 unreferenced remain, 0 referenced query images missing |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 425 passed |

### Notes

- Two `face_samples` rows reference images absent from the container's object
  store. Both have `source: 'test'`: the integration tests write rows into the
  shared development database while storing their images in a per-test
  temporary directory. Not a defect, but it is a reason to give the tests their
  own database before this gets confusing.

### Known gaps after Phase 11

- No per-person access control; no merge/split; thresholds unvalidated; model
  weight provenance unresolved.
- Integration tests share the development database, leaving rows behind.

## Phase 12 — Password accounts — ✅ COMPLETE

Requested explicitly, after I recommended tokens and the preference was
repeated. Password login mints an ordinary session credential rather than
introducing a second way to authenticate.

### Delivered

| Item | Location |
| --- | --- |
| Accounts, Argon2id hashing, policy, constant-cost failure | `backend/app/domain/users.py` |
| Account storage | `backend/app/connectors/postgres/users.py` |
| Sign-in service, session minting, password change | `backend/app/services/authentication.py` |
| `POST /sessions`, `DELETE /sessions/current` | `backend/app/api/v1/sessions.py` |
| Account administration CLI | `backend/app/users.py` |
| Session-bound credentials (`api_tokens.user_uuid`) | `backend/app/domain/auth.py` |
| Email + password sign-in, revoking sign-out | `frontend/src/app/{sign-in,sign-out}` |
| Schema invariant tests | `backend/tests/test_schema_invariants.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| A correct password mints a working credential | live sign-in then `/me` | ✅ 201 then 200 |
| The credential carries the account's scopes | live | ✅ admin, enrol, identify, review |
| Wrong password is refused | live | ✅ 401 `sign_in_failed` |
| Unknown email is indistinguishable | live, same code and message | ✅ |
| A missing account still costs a verification | timing test | ✅ no fast path |
| Repeated failures are throttled | integration test | ✅ 10 × 401 then 429 |
| Disabled accounts cannot sign in | integration test | ✅ |
| The plaintext is never stored | table inspection | ✅ Argon2 hash only |
| The password is never echoed in a response | endpoint test | ✅ |
| Two accounts sharing a password do not look alike | distinct salts | ✅ |
| Signing out revokes server-side | live | ✅ 200 then 401 |
| Changing a password ends existing sessions | live | ✅ 3 revoked, old token 401, new login 201 |
| Sign-in through the browser | real browser | ✅ signed in as admin@admin.com |
| Lint, types and tests clean | ruff, mypy --strict, pytest, tsc, eslint, vitest | ✅ 478 backend, 28 frontend |

### A defect this phase exposed

Autogenerating the migration revealed that **my Phase 9 fix had only ever been
half-applied**. The edit removing `identifications.best_person_uuid`'s foreign
key never matched, so the constraint was dropped from the database by a
hand-written migration while the model kept declaring it. Every behavioural
test passed, because they all ran against the already-migrated database — and
this phase's autogenerate duly proposed re-creating the constraint, briefly
restoring the 500 it had caused.

Corrected: the model no longer declares it, the migration is amended, the
database reconciled, and `alembic check` reports no drift.
`tests/test_schema_invariants.py` now asserts on the table *definitions*, which
is the check that was missing.

### Known gaps after Phase 12

- No password reset flow, no MFA, no session listing for a user.
- Integration tests shared the development database — **fixed in Phase 13**.
- No per-person access control; no merge/split; thresholds unvalidated; model
  weight provenance unresolved.

## Phase 13 — Administration API — ✅ COMPLETE

Account and credential administration over HTTP, so neither requires shell
access to the container.

### Delivered

| Item | Location |
| --- | --- |
| Account and credential administration with auditing | `backend/app/services/administration.py` |
| `/accounts`, `/accounts/{uuid}/password`, `/disable`, `/enable` | `backend/app/api/v1/administration.py` |
| `/tokens`, `/tokens/{uuid}`, `/me/password` | `backend/app/api/v1/administration.py` |
| Six new audit actions | `backend/app/domain/audit.py` |
| Test-database guard | `backend/tests/conftest.py` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| An account can be created over HTTP and sign in | live | ✅ 201 then 201 |
| An issued credential works immediately | live `/me` | ✅ right subject and scopes |
| Revocation takes effect at once | live | ✅ 204 then 401 |
| Secrets are returned once, never listed | endpoint test and live | ✅ |
| Administration requires the `admin` scope | live with a reviewer session | ✅ 403 on accounts and tokens |
| Changing your own password needs no admin scope | endpoint test | ✅ 404 not 403 |
| The current password is required to change your own | endpoint test | ✅ 403 `wrong_password` |
| You cannot disable your own account | live | ✅ 409 `account_conflict` |
| Duplicate emails and weak passwords are refused | endpoint tests | ✅ 409, 422 |
| Every action is audited against the administrator | live audit read | ✅ actor recorded |
| The audit never records a secret or password | endpoint test | ✅ |
| Disabling reports the account's new state | endpoint test | ✅ (bug found and fixed) |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 505 passed |

### Notes

- **The tests had destroyed live data three times** by truncating tables in the
  working database, including the account created minutes earlier. The suite
  now refuses to run unless `FACEID_TEST_POSTGRES_DSN` names a database ending
  in `_test`, which makes the accident impossible rather than merely
  documented.
- `set_disabled` returned the account as read *before* the update, so the
  response claimed an account was still active immediately after disabling it.
  Caught by an endpoint test, fixed by re-reading.

### Known gaps after Phase 13

- No password reset flow, no MFA, no per-user session listing.
- No UI for administration; it is API and CLI only.
- No per-person access control; no merge/split; thresholds unvalidated; model
  weight provenance unresolved.

## Phase 14 — Read APIs — ✅ COMPLETE

Records the system already kept but could not show. Four read-only endpoints,
each paginated, each scoped.

### Delivered

| Item | Location |
| --- | --- |
| Read-side queries (aggregate, paginate, join) | `backend/app/connectors/postgres/queries.py` |
| `GET /persons`, `GET /persons/{uuid}` | `backend/app/api/v1/browse.py` |
| `GET /identification-history` | same |
| `GET /audit-events` | same |
| `GET /statistics` | same |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| People list with samples and identifiers | integration tests | ✅ counts and identifiers correct |
| Search matches a uuid or an external identifier | integration tests | ✅ |
| Pagination reports the full total | integration test | ✅ 2 total, 1 returned |
| Invalid paging is refused | parametrised | ✅ 422 |
| One person reads back with their samples | integration test | ✅ |
| History includes what the review queue excludes | integration test | ✅ decided identifications visible |
| History filters by outcome, review state and person | integration tests | ✅ |
| Audit lists most recent first with its actions | integration tests | ✅ |
| Audit filters by action, actor and person | integration tests | ✅ |
| A system actor stays distinguishable from a person | integration test | ✅ |
| Nothing can change an audit event | POST and DELETE attempted | ✅ 405 |
| Statistics are real counts | integration test | ✅ matches seeded data |
| An empty system reports zeroes | integration test | ✅ not an error |
| Browsing needs `review`; audit needs `admin` | scope tests | ✅ 403 otherwise |
| Lint, types and tests clean | ruff, mypy --strict, pytest | ✅ 529 passed |

### Notes

- `/identification-history` is separate from `/identifications` rather than a
  flag on it. The queue's contract — unreviewed proposals only — is relied on
  by the review screen, and overloading it would have made the default
  behaviour depend on a parameter's absence.
- Statistics are **counts only**. No rates, trends or indicators, because those
  need a time series the system does not keep, and a plausible-looking trend
  line would be an invention.
- The audit log gained a read endpoint but no write, edit or delete. It stays
  append-only by construction.

## Phase 15 — Dhivehi language search slice — 🟡 PARTIAL

The repository now contains the first language-intelligence vertical slice:
Thaana, Latin/Romanized Dhivehi, English, and mixed-script normalization;
baseline bidirectional transliteration; versioned multilingual E5 embeddings;
chunked Qdrant indexing; semantic document search; API endpoints; a worker; and
a frontend language workspace.

This phase is intentionally not marked complete. It lacks a versioned Dhivehi
retrieval/transliteration benchmark, PostgreSQL chunk/offset persistence,
citation-ready results, document list/delete/reindex administration, committed
real-store end-to-end evidence, LLM understanding, and stylometry. Those gaps
are planned in master Phases M5, M6, M12, and M13.

## Master Phase M0 — Trustworthy baseline and architecture record — ✅ COMPLETE

Selected on 2026-08-28 after the repository-wide audit. This phase changes
documentation only: it records what is complete/partial/missing/unknown, defines
the target trust and persistence boundaries, and orders future work by
dependency. Runtime code, migrations, API contracts, models, and deployment are
out of scope.

Delivered files:

- `docs/REPOSITORY_AUDIT.md`
- `docs/MASTER_IMPLEMENTATION_PLAN.md`
- reconciled README and implementation-status language

Acceptance evidence:

- `git diff --check`: passed.
- Base plus deployment Docker Compose configuration: passed.
- Plan schema check: all 19 phases contain every required field.
- Secret-pattern check over changed documentation: passed; the supplied API key
  is not present.
- Backend Ruff and format check: passed (119 files).
- Backend strict mypy: passed (109 source files).
- Focused backend contracts: 24 passed, including all 10 language tests.
- Frontend TypeScript, ESLint, and Vitest: passed (74 tests).
- Full backend execution was not claimed for M0 because its legacy virtual
  environment was incomplete; M1 establishes a fresh verified runtime baseline.

## Master Phase M1 — Durable processing core — ✅ COMPLETE

Completed on 2026-08-29. PostgreSQL is now the authoritative job store for
face enrolment and language indexing. Domain state and job creation are atomic;
workers claim prioritized jobs with leases and fencing, record immutable
attempts, retry typed transient failures with bounded backoff, dead-letter
exhausted work, and publish transactional outbox rows for every transition.
Redis remains only for rate limiting and conversion of legacy queue entries.

### Delivered

| Item | Location |
| --- | --- |
| Stable processing domain contract and error taxonomy | `backend/app/domain/processing.py` |
| Durable tables, repository, claim consumer, metrics and outbox | `backend/app/connectors/postgres/processing_tables.py`, `jobs.py`, `job_queue.py` |
| Admin job list/detail/summary/retry/cancel with audit | `backend/app/services/processing.py`, `backend/app/api/v1/processing_jobs.py` |
| Atomic face/language producers and lease-aware workers | enrolment/language services, `worker.py`, `language_worker.py` |
| Upgrade/downgrade migration | `backend/migrations/versions/79b8c31f4d2a_durable_processing_jobs.py` |
| Live dashboard processing telemetry | `frontend/src/components/hardware-usage.tsx` |
| Operator/state-machine documentation | `docs/PROCESSING_JOBS.md` |

### Acceptance evidence

- Clean migration rehearsal: prior head → M1 → prior head → M1 on disposable
  PostgreSQL; all four `processing` tables were present after upgrade.
- Focused M1/domain/API integration suite: 42 passed.
- Redis/object compatibility suite: 30 passed.
- Real language job: API → PostgreSQL → worker → Qdrant completed with model
  provenance recorded.
- Real face job: API → shared object storage → SCRFD/AdaFace worker → Qdrant
  completed with a 512-dimensional AdaFace embedding and one attempt.
- A deliberately unavailable face object produced the typed
  `source_unavailable` failure instead of losing or falsely completing work.
- Live metrics reported completed/failed counts and two live worker heartbeats.
- Full backend regression: 526 passed, 62 skipped in 54.68 seconds.
- Ruff, Python compilation, tracked-secret scan, and Ruff security rules passed.
- Frontend TypeScript, ESLint, 74 Vitest tests, and production Next.js build
  passed.
- Base plus deployment Compose configuration passed.
- Live localhost checks returned 200 for health/readiness and 401 for an
  unauthenticated processing-admin request.
- Strict mypy reached 83 backend modules; only the pre-existing third-party
  `onnxruntime` and `sentence_transformers` missing-type-metadata errors remain.
- `pip-audit`, Bandit, and Trivy are not installed, so no dedicated dependency
  vulnerability scan is claimed.

See `docs/PROCESSING_JOBS.md` for lease, retry, recovery, alerting, cutover, and
rollback procedures. Master Phase M2 is recommended next; it is not started.
