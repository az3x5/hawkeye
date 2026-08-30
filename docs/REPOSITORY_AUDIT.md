# EagleEye repository audit

Audit date: 2026-08-28
Audited commit: `68d10f9` (`main`, aligned with `origin/main`)
Scope: repository source, declared migrations, tests, Docker configuration, CI, local runtime state, and reachable deployment state.

This is the evidence baseline for `MASTER_IMPLEMENTATION_PLAN.md`. A feature is not marked complete because a directory, screen, or placeholder exists. `COMPLETE` means the repository contains an implemented path with proportionate automated evidence. `PARTIAL` means a useful vertical slice exists but one or more production requirements are absent. `UNKNOWN` means the repository cannot prove the live state.

## Executive assessment

EagleEye is currently a credible face-identity application with authentication, enrolment, identification, human review, audit, retention/erasure, administration, read APIs, operational metrics, and a first Dhivehi language-search slice. It is not yet the multimodal intelligence platform described by the master prompt.

The best next engineering investment is not another AI model. It is a durable processing core: persistent jobs, leases, recovery, retry policy, model provenance, and domain-separated storage. Without that foundation, OCR, media intelligence, video, audio, and BlackGlass ingestion would multiply the current queue and observability risks.

## Audit limits

- This audit inspected the declared schema in SQLAlchemy and every Alembic revision. It did not claim that a currently running database matches that schema.
- The `cyber-ai` host at `100.74.113.94` was not reachable during this audit, so its running commit, migration revision, containers, queues, model weights, and data health are `UNKNOWN`.
- Local Docker containers belong to another checkout (`/home/axmyn/Downloads/face-id`), not this repository. They are not evidence for EagleEye runtime health.
- The latest repository CI and image-publish workflows for commit `68d10f9` passed. That is build evidence, not a substitute for a fresh production deployment verification.
- The repository was renamed from `/home/axmyn/Projects/AI` to `/home/axmyn/Projects/EagleEye`. The ignored root virtual environment still contains old absolute paths and must be rebuilt before relying on its console scripts.

## Classification summary

| Capability | State | Evidence / reason |
| --- | --- | --- |
| FastAPI service foundation | COMPLETE | Application factory, structured errors/logging, readiness probes, strict settings, tests |
| PostgreSQL identity model | COMPLETE | Nine declared tables, nine linear Alembic revisions, schema invariant tests |
| Face detection and recognition | COMPLETE | SCRFD and AdaFace adapters, checksum checks, provenance, real-model tests when weights exist |
| Face enrolment and identification | COMPLETE | API, services, Redis work queue, worker, Qdrant search, tests |
| Human review, audit, retention, erasure | COMPLETE | API/UI paths, append-only audit design, reconciliation, tests |
| Authentication and administration | COMPLETE | Argon2id accounts, hashed/expiring tokens, scopes, rate limits, admin APIs and UI |
| People/history/statistics UI | COMPLETE | Read APIs and Next.js routes exist with frontend tests |
| Operational dashboard metrics | PARTIAL | Authenticated snapshots exist; no time-series metrics, request IDs, alerts, or Prometheus endpoint |
| Camera motion/person tracker | PARTIAL | Browser frame-difference gate plus full-frame face identify; no real object/track lifecycle or persistent sightings |
| Dhivehi NLP | PARTIAL | Thaana/Latin/mixed normalization, rule transliteration, E5 embeddings and search exist; no corpus benchmark, LLM, citations, stylometry, or administration lifecycle |
| Durable processing jobs | PARTIAL | Redis reliable lists exist; no persistent job record, lease expiry, automatic reclaim, priority, bounded retry, or dead-letter administration |
| Media/object storage | PARTIAL | SHA-addressed filesystem connector exists; no first-class media rows, provenance graph, MinIO, S3 adapter, or lifecycle API |
| Vector storage isolation | PARTIAL | Face provenance gets distinct collections, but face and general language vectors share one Qdrant service |
| BlackGlass integration | MISSING | No client, credentials seam, mapping table, ingestion endpoint, sync cursor, contract tests, or audit actions found |
| OCR and document intelligence | MISSING | No OCR adapter/model/worker/table/API/UI; no OCR weights present |
| General image intelligence | MISSING | No SigLIP/VLM pipeline, image observations, captions, or search |
| Vehicle intelligence | MISSING | No vehicle entities, detections, attributes, embeddings, or review |
| Video intelligence | MISSING | No video asset model, decoding, shots, tracks, observations, or streaming ingestion |
| Audio intelligence | MISSING | No audio assets, STT, diarization, speaker embeddings, segments, or search |
| Universal entity/evidence layer | MISSING | No universal entities, relationships, observations, claims, evidence links, confidence/provenance model, or analysis runs |
| Model registry | MISSING | Per-result model strings exist, but no queryable registry, deployment state, artifact/checksum/license record, or lineage API |
| Local production-like Compose | PARTIAL | Core seven-service topology validates; required MinIO, isolated Qdrant domains, specialist workers, and hardened profiles are absent |
| AWS portability | MISSING | No S3/managed-Postgres/ElastiCache/OpenSearch-or-Qdrant adapters or deployment notes |
| Security program | PARTIAL | Strong application basics; missing explicit data classification, biometric policy controls, SSRF-safe ingestion, automated security gates, backup/restore tests, and threat-model test plan |
| Documentation | PARTIAL | Detailed historical phases exist, but README and architecture contain stale status/planned statements and omit the new platform roadmap |

## Current repository architecture

### Services and directories

- `backend/app/api/v1`: FastAPI routers and schemas.
- `backend/app/services`: enrolment, identification, authentication, administration, erasure, browsing support, and language orchestration.
- `backend/app/domain`: framework-independent identity, detection, recognition, vectors, jobs, users, audit, and language types.
- `backend/app/adapters`: SCRFD, AdaFace/IR-101, preprocessing, and adapter construction.
- `backend/app/connectors/postgres`: SQLAlchemy Core tables, repositories, and read queries.
- `backend/app/connectors/qdrant`: face and language vector repositories.
- `backend/app/connectors/redis`: rate limiting and face/language queues.
- `backend/app/connectors/filesystem`: content-addressed local object storage.
- `backend/app/worker.py`: face embedding plus housekeeping worker.
- `backend/app/language_worker.py`: language embedding worker.
- `backend/migrations`: Alembic configuration and revisions.
- `backend/tests`: 31 test modules covering the currently implemented scope.
- `frontend/src/app`: dashboard, people, enrolment, identification, tracker, review, matches/history, audit, settings, language, and authentication routes.
- `scripts`: deployment and operational/import scripts; two operational scripts are currently untracked.
- `docs`: architecture, API examples, and historical implementation status.

### Runtime topology

The base Compose file defines `api`, `frontend`, `worker`, `language-worker`, `postgres`, `redis`, and `qdrant` on one `faceid` network. The API and frontend bind to loopback; Postgres is also loopback-published for local development. Redis and Qdrant are internal-only. Model files are bind-mounted read-only; source images use a named filesystem volume; the language model cache uses a named volume.

The deployment overlay adds resource ceilings. It does not add domain network segmentation, TLS, a reverse proxy, MinIO, a second Qdrant service, backup jobs, or specialist media workers. `docker compose ... config --quiet` succeeds for the base plus deployment overlay.

### Request and work flow

1. A browser talks only to the Next.js runtime proxy.
2. The proxy forwards an allowlisted API surface and holds the credential in an HTTP-only cookie.
3. FastAPI authenticates/scopes the operation and uses PostgreSQL as metadata system of record.
4. Enrolment stores image bytes by SHA-256, commits person/sample metadata, and pushes an embedding job to Redis.
5. The worker detects one face, aligns it, produces an AdaFace embedding, writes Qdrant, records provenance in PostgreSQL, and marks the sample processed.
6. Identification performs detection/embedding in the API request path, searches the provenance-specific face collection, applies explicit decision policy, stores the decision/query image, and routes uncertain results to review.
7. Language ingestion normalizes/chunks text, commits a language document, queues work, embeds chunks using multilingual E5, and indexes them in a language collection in the same Qdrant service.

## Declared PostgreSQL schema

Alembic head is `e841b65a8e12`. The chain is:

`5e5780ed997c -> b8265d112b06 -> dcece6ee12f6 -> 35141621313d -> a1e093fc7216 -> 6904bc245c44 -> cde2c7d24322 -> b09c07269569 -> e841b65a8e12`

All current tables use PostgreSQL's default `public` schema.

| Table | Purpose | Important relationships / constraints |
| --- | --- | --- |
| `persons` | Internal person identity | UUID is the only internal key |
| `person_external_identifiers` | Source-scoped upstream IDs | FK to person with cascade; unique `(source, kind, value)` |
| `face_samples` | One person to many source images | FK to person with cascade; unique person/content hash; processing state |
| `face_embeddings` | Provenance and vector location | FK to sample with cascade; unique sample/model/version/preprocessing |
| `identifications` | Historical identity decisions | Intentionally no person FK; thresholds, candidates and review outcome retained |
| `audit_events` | Append-only administrative/decision audit | Intentionally no FKs so erasure remains auditable |
| `api_tokens` | Hashed user/service credentials | Scopes, expiry, disable time; optional user UUID intentionally not constrained |
| `users` | Password accounts | Normalized unique email; Argon2id hash and account state |
| `language_documents` | Source text and embedding provenance | Unique source/content hash; script and processing-state constraints |

Missing persistence domains include media/assets, content/chunks in PostgreSQL, universal entities, observations, relationships, claims/evidence, events/narratives, persistent processing jobs, analysis runs, model registry/deployments, BlackGlass mappings/sync cursors, vehicles, video tracks, audio segments, OCR pages/regions, access-control policy, and retention/legal-hold metadata.

Introducing schemas is not automatically an improvement. Moving these mature public identity tables would be a high-risk migration with little immediate benefit. New domains should use schemas only where ownership, privilege boundaries, or lifecycle justify them; physical databases/services should provide the biometric/general vector boundary.

## API surface

Implemented router groups include:

- Health: liveness and dependency readiness.
- Sessions and identity: sign in, sign out, current actor, password change.
- System: authenticated CPU/RAM/disk/GPU/queue metrics snapshot.
- Enrolment: submit a face sample, read processing state, read a sample image.
- Identification: identify, list/retrieve decisions, retrieve query/candidate images, review.
- People/read model: list/detail/delete people, history, audit events, and statistics.
- Administration: create/list/disable/enable accounts, change passwords, issue/list/revoke credentials.
- Language: normalize, transliterate, ingest a document, read its state, semantic search.

Missing API domains are media upload/fetch lifecycle, OCR, general image analysis, vehicles, video, audio, entities/relationships, evidence/claims, events/narratives, universal search, jobs/retries, model registry, integrations/BlackGlass, backup/restore health, and policy/classification administration.

## Models and weights

| Model | State | Loading and provenance |
| --- | --- | --- |
| SCRFD 10G ONNX | COMPLETE for current face scope | Local mounted weight, optional required checksum setting, ONNX Runtime provider configuration, model hash exposed as version |
| AdaFace IR-101 WebFace12M | COMPLETE for current face scope | Local safetensors weight, checksum, strict state loading, CPU/GPU device setting, 512-d normalized vector |
| `intfloat/multilingual-e5-small` | PARTIAL platform scope | Pinned Hugging Face revision and cache volume; useful semantic baseline but not benchmarked against BGE-M3 on a Dhivehi retrieval corpus |
| OCR | MISSING | No adapter, configuration, weights, or tests |
| SigLIP / general image embeddings | MISSING | No adapter or collection |
| Vision-language model | MISSING | No adapter, local-serving contract, or prompt/version lineage |
| Video models | MISSING | No detector/tracker/shot/action pipeline |
| Whisper / diarization / speaker embeddings | MISSING | No audio pipeline |
| Local LLM | MISSING | No local inference provider, RAG contract, structured-output validation, or evidence citation model |

The repository records face embedding provenance well, and language documents record their embedding model/version. It does not have a central model registry covering artifact digest, source, license, task, dimensions, runtime, configuration, activation state, evaluation, or deployment history. Face thresholds are configuration and stored with decisions, but no production calibration dataset/report proves that the chosen values meet an operational false-match/false-non-match target.

## Queues and workers

Redis lists provide pending, in-flight, and failed states for face and language work. Reservation uses a blocking move, which is safer than destructive pop. However, no automatic lease expiration or reclaim moves abandoned in-flight work after a crash. There is no persistent processing-job row, attempt history, priority, next-attempt time, idempotency key across domains, cancellation, worker heartbeat, bounded backoff, dead-letter administration, or job dependency graph. The language worker also uses an active set that may become stale.

This is a known operational issue, not theoretical debt: prior enrollment sessions stalled while jobs remained in-flight. Scaling workers or adding more pipelines before recovery and backpressure exist would make the failure mode larger.

## Vector and object storage

Face vectors use collections derived from the complete model provenance triple, preventing cross-model comparisons. Language vectors use separate collection names and payloads, but share the same Qdrant process, credentials, network, backups, and failure domain as biometrics. The target architecture requires separate biometric and general vector services/credentials.

The filesystem object store is content-addressed and supports erasure reconciliation. It is a good interface seed, but it is not a media system: images are not first-class assets, there are no immutable source/derived relationships, content type/dimensions/duration, provenance, classification, legal hold, signed access, lifecycle rules, or S3/MinIO adapter.

## Frontend and BlackGlass boundary

The existing Next.js application is a broad operational console for current features. Its runtime proxy is an important security boundary: the browser has no direct Postgres/Qdrant/API credential access and only allowlisted paths are forwarded.

The tracker route is not a production person-tracking system. It performs motion gating in the browser and submits frames for face identification. It lacks camera-source records, RTSP ingestion, multi-object tracking, stable track IDs, crops/observations, time-space sightings, replay, and retention controls.

No BlackGlass implementation was found. BlackGlass should remain an external system integrated only through versioned APIs and scoped credentials. The plan must not introduce shared tables, direct database reads, or a merged frontend. EagleEye needs explicit external-reference mappings, idempotent ingestion/synchronization, audit trails, rate limiting, timeouts, and contract tests against a stub server.

## Tests and delivery

The backend has 31 test modules spanning unit, API, seam, model, queue/object, and opt-in integration tests. Tests guard against accidentally using a non-`_test` database. The frontend uses strict TypeScript, ESLint, Vitest, and a production build in CI. GitHub Actions runs backend lint/format/type/tests against real Postgres/Redis/Qdrant and runs frontend types/lint/tests/build. The current commit's CI and publish workflows passed.

Important missing evidence:

- committed end-to-end tests for Dhivehi ingestion/search against real stores;
- queue crash/reclaim and retry/backpressure integration tests;
- MinIO/S3 and domain-isolated Qdrant tests;
- BlackGlass contract, auth, retry, idempotency, and failure tests;
- media provenance and delete/retention tests;
- benchmark/evaluation corpora for Dhivehi retrieval, OCR, face thresholds, and later modalities;
- security gates and authorized local DAST;
- backup and restore drills;
- load/soak tests and resource budgets by worker type.

## Security posture

### Existing controls

- Environment-driven secrets; `.env` is ignored.
- Argon2id passwords; bearer tokens stored only as SHA-256 digests; expiry and revocation.
- Scope checks and rate limiting around sensitive operations.
- HTTP-only frontend credential cookie and narrow server-side proxy.
- Structured audit events for administration, review, erasure, and reconciliation.
- Biometric fields stripped from structured logs.
- Model artifacts mounted read-only and checksum-verifiable.
- Non-root backend/frontend containers.
- Redis and Qdrant not host-published in the base topology.
- Historical decisions/audit survive person erasure while active biometric artifacts are reconciled.

### Gaps and risks

- Scopes are coarse; no per-case, per-source, classification, purpose, or person policy.
- No explicit reference-biometric versus observed-biometric model or access boundary.
- General and biometric vectors share infrastructure.
- No first-class consent/legal basis, retention class, legal hold, or deletion workflow for future media.
- Future remote URL ingestion would create SSRF risk; no downloader exists yet and one must not use a generic HTTP client without DNS/IP revalidation, redirect policy, size/type limits, and tests.
- No Semgrep/Bandit/pip-audit/npm-audit policy, Gitleaks, container scan, IaC scan, SBOM, image signing, or local DAST gate.
- No built-in TLS/reverse proxy; current deployment assumes a trusted overlay/network.
- No MFA, reset flow, session inventory, or reauthentication policy for destructive actions.
- No backup/restore scripts and no tested recovery objectives.
- No request/correlation IDs, metrics time series, alerting, or audit export/integrity mechanism.
- The worktree contains untracked `CSV/`, a session-like `backend/:memory:.ses`, an importer overlay, and operational scripts. Their ownership and sensitivity must be resolved before any broad commit.

Security testing is authorized only against localhost and local Docker by default. The remote `cyber-ai` host and BlackGlass are not automatically in scope for active testing.

## Architecture and documentation debt

- Source still uses legacy `Hawkeye`/`FACEID_` identifiers. The branding rule permits compatibility names to remain; new platform-facing names should use EagleEye and migration/alias decisions must be explicit.
- README reports Phase 11 while the status file reaches Phase 14 and the repository contains a newer language feature.
- `ARCHITECTURE.md` has stale statements claiming recognition, identity decisions, services, embedding metadata, or audit are planned.
- `IMPLEMENTATION_STATUS.md` is historically valuable but lacks the Dhivehi language phase and the new platform roadmap.
- Deployment docs use `~/hawkeye`, legacy image names, and legacy environment prefixes without explaining compatibility.
- There is no data classification, threat model, security test plan, model-card inventory, backup/restore runbook, BlackGlass contract, or AWS portability note.

## Recommended sequence

1. Complete Phase M0, the documentation and trustworthy-baseline phase represented by this audit and the master plan.
2. Build Phase M1 durable processing before adding any new AI pipeline.
3. Add first-class media/storage abstraction and domain isolation.
4. Establish the BlackGlass contract and mapping boundary.
5. Upgrade content/search and then add OCR as the first new modality.
6. Add image, vehicle, video, and audio pipelines through the same job/media/provenance contracts.
7. Add universal entities, evidence/claims, model registry, LLM/RAG, events/narratives, and analyst workflows.
8. Finish with explicit security hardening, performance/DR verification, and AWS deployment readiness.

The phase order is a dependency order, not a promise to install every heavyweight model. Every candidate model must pass a local benchmark and resource review before replacing a working baseline.
