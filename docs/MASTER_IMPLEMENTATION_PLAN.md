# EagleEye master implementation plan

Plan date: 2026-08-31
Evidence baseline: `docs/REPOSITORY_AUDIT.md`
Completed phases: **M0**, **M1 — Durable processing core**, **M2 — First-class media and local object storage**

## Product boundary and non-negotiable principles

EagleEye is a local-first multimodal intelligence platform. BlackGlass is an external product. Integration occurs through authenticated, versioned APIs; neither product reads the other's database or shares internal tables.

The following rules apply to every phase:

1. PostgreSQL is the metadata system of record. Object storage owns immutable media bytes and derived artifacts. Qdrant owns vectors only. Redis coordinates ephemeral work and caching, never the only durable record of business state.
2. Biometric and general-purpose vectors use separate Qdrant services, credentials, networks, backups, and retention policy.
3. Reference biometrics (deliberately enrolled) and observed biometrics (extracted from other media) are different data classes. They require explicit linkage, authorization, retention, and audit.
4. Raw artifacts are immutable. Derived artifacts link to source, transform, model, version, parameters, and analysis run. Reprocessing creates new lineage instead of silently overwriting old results.
5. AI adapters expose stable domain contracts. Provider-specific code stays behind adapters. Model choice is benchmark-driven; the plan does not force installation of BGE-M3, a VLM, or an LLM before evidence shows value.
6. Human review is required for consequential or uncertain outputs. Similarities and confidence values are not presented as probabilities unless calibration proves that interpretation.
7. Every mutating or sensitive read path is authenticated, authorized, rate-limited where appropriate, and audited without logging secrets or biometric payloads.
8. Local Docker is the first deployment target. AWS compatibility is preserved with storage/queue/model interfaces and documented mappings, not by making cloud services a development dependency.
9. New names use EagleEye. Legacy `Hawkeye`, image names, and `FACEID_` settings remain only where changing them would break compatibility; aliases/deprecations must be documented.
10. Active security testing is restricted to localhost and local Docker unless an explicit host allowlist is recorded.

## Target logical architecture

```text
Browser -> EagleEye web/BFF -> FastAPI API
                              |-- PostgreSQL (metadata, policy, lineage, audit)
                              |-- Redis (coordination/cache only)
                              |-- MinIO Object API (source + derived artifacts)
                              |-- Qdrant biometric (face/speaker reference + observed)
                              `-- Qdrant general (text/image/video search)

API -> transactional job/outbox -> domain workers
       |-- face worker          |-- OCR worker
       |-- language worker      |-- image/VLM worker
       |-- vehicle worker       |-- video worker
       `-- audio worker         `-- entity/evidence worker

BlackGlass <-> versioned EagleEye integration API (scoped credentials,
               mappings, cursors, idempotency, audit; never shared storage)
```

## Target persistence domains

Existing identity tables remain in `public` until a separately rehearsed migration proves benefit. New logical schemas are justified where they express ownership or privilege boundaries:

| Schema/domain | Principal tables | Notes |
| --- | --- | --- |
| `public` legacy identity | current nine tables | Preserve compatibility; do not rename in place casually |
| `core` | `entities`, `external_identifiers`, `entity_relationships`, `entity_merge_history` | Universal identity without making external IDs keys |
| `media` | `assets`, `asset_derivatives`, `asset_locations`, `asset_access_events`, `retention_holds` | Immutable hashes, provenance, classification, lifecycle |
| `content` | `documents`, `content_chunks`, `ocr_pages`, `ocr_regions`, `transcripts`, `transcript_segments` | Searchable text with source offsets/citations |
| `analysis` | `analysis_runs`, `observations`, `claims`, `evidence_links`, `model_registry`, `model_deployments` | Versioned outputs and evidence lineage |
| `processing` | `jobs`, `job_attempts`, `worker_heartbeats`, `outbox_events` | Durable state, leases, retries, priority, cancellation |
| `integration` | `systems`, `credentials_metadata`, `external_mappings`, `sync_cursors`, `delivery_attempts` | No secret values in tables; secrets come from environment/secret store |

This is a design target, not authorization to create every table at once. Each phase adds only the smallest migration needed for its vertical slice.

## Phase dependency map

```text
M0 baseline
 `-> M1 durable processing
      `-> M2 media/storage -----> M4 BlackGlass
             |                  `-> M5 content/search -> M6 OCR
             |                                      `-> M7 image -> M8 vehicle
             |                                      `-> M9 video
             |                                      `-> M10 audio
             `-> M3 trust boundaries/security foundation

M6..M10 -> M11 entities -> M12 evidence/model registry -> M13 LLM/RAG
                                                   `-> M14 events/narratives
M11..M14 -> M15 analyst workflows -> M16 security verification
                                    -> M17 performance/DR -> M18 AWS readiness
```

## M0 — Trustworthy baseline and architecture record (selected)

**Goal**
Turn the audited repository state into an authoritative, reviewable baseline before changing runtime behavior.

**Dependencies**
Current `main` at `68d10f9`; repository and declared schema readable.

**Scope**

- Record current implementation, schema, services, routes, models, workers, tests, security controls, and gaps.
- Publish this dependency-ordered plan.
- Reconcile top-level status language so it no longer claims Phase 11 is current.
- Record the Dhivehi language slice without overstating it.
- Record environment and untracked-artifact risks; do not delete or commit user data.

**Out of scope**

- Runtime code, migrations, API changes, new services/models, dependency upgrades, deployment, or security scanning of remote hosts.
- Deleting/rebuilding the broken virtual environment in this documentation-only phase.

**Services/components affected**
Documentation and repository metadata only.

**Database changes**
None.

**API changes**
None.

**Model/AI changes**
None.

**Security/privacy implications**
Documents untracked data/credential/session risks without reading sensitive contents into logs or committing them.

**Tests required**

- `git diff --check`.
- Docker Compose configuration validation.
- Link/path review for added documentation.
- Existing CI status recorded; no claim of new runtime verification.

**Acceptance criteria**

- Repository audit exists and uses `COMPLETE/PARTIAL/MISSING/BROKEN/UNKNOWN` consistently.
- Master plan exists and every phase contains the required planning fields.
- README/current status points to the audit and plan.
- No runtime code, migration, model, or deployment state changed.

**Documentation required**
`REPOSITORY_AUDIT.md`, this file, and concise README/status updates.

**Rollback plan**
Revert the documentation commit; runtime is unaffected.

## M1 — Durable processing core — COMPLETE

**OBJECTIVE**
Make face and language work recoverable, observable, idempotent, prioritized,
and safe under worker crashes before adding more pipelines.

**CURRENT STATE**
PostgreSQL is authoritative for queued work. Producers insert domain state and
the corresponding job in one transaction. Workers claim with row locks,
bounded leases, and fencing tokens; record every attempt; retry retryable
failures with bounded exponential backoff; and move exhausted work to
`dead_letter`. Redis remains only for rate limiting and draining legacy queue
entries during the cutover. The dashboard reads live processing telemetry.

**FILES EXPECTED TO CHANGE**
API dependency wiring and metrics; face/language services and workers; Redis
legacy queue adapters; runtime configuration; Compose; dashboard types and
hardware/processing telemetry; schema invariants and integration tests.

**FILES EXPECTED TO CREATE**
Processing domain contracts, PostgreSQL processing tables/repository/consumer,
processing service and admin router, Alembic revision, focused processing
tests, and `docs/PROCESSING_JOBS.md`.

**DATABASE CHANGES**
Revision `79b8c31f4d2a` creates `processing.jobs`,
`processing.job_attempts`, `processing.worker_heartbeats`, and
`processing.outbox_events`, including claimable-state, priority, lease,
attempt, delivery, and idempotency indexes. Its downgrade removes the schema.

**API CHANGES**
Adds admin-scoped `GET /api/v1/processing/jobs`,
`GET /api/v1/processing/jobs/summary`,
`GET /api/v1/processing/jobs/{job_uuid}`,
`POST /api/v1/processing/jobs/{job_uuid}/retry` (from failed, dead-letter, or
cancelled), and
`POST /api/v1/processing/jobs/{job_uuid}/cancel`. Existing enrolment and
language contracts remain compatible. `/api/v1/system/metrics` now includes
durable processing counts, queue age, lease, throughput, and worker telemetry.

**AI/MODEL CHANGES**
No model or threshold was changed. Existing SCRFD/AdaFace and multilingual E5
adapters now consume the same durable envelope through their workers.

**BLACKGLASS INTEGRATION CHANGES**
No BlackGlass endpoint or behavior was added. Transactional outbox rows provide
the durable event boundary needed by a later authenticated BlackGlass relay.

**SECURITY CONSIDERATIONS**
Jobs reject binary payloads and carry opaque identifiers instead of media,
vectors, raw documents, credentials, or tokens. Outbox events omit job payloads.
Administrative reads and mutations require `admin`; retry/cancel are audited.
Lease ownership and fencing prevent stale workers from committing outcomes.

**TESTS**
Domain validation, idempotent enqueue, priority claim, concurrent claim,
lease expiry/reclaim/fencing, completion, retry budget/backoff, typed failure,
dead letter, cancellation, outbox transitions, API scope/audit, schema
invariants, legacy Redis recovery, migration upgrade/downgrade, full backend
regression, frontend type/lint/unit/build, Compose validation, and real
PostgreSQL/Redis/Qdrant face and language smoke tests.

**ACCEPTANCE CRITERIA**
Verified: a killed worker cannot strand a job after its lease expires; every
attempt and transition is queryable; duplicate logical submissions converge;
stale completions are fenced; operator retry/cancel is scoped and audited;
failed/dead counts, queue age, throughput, leases, and worker heartbeats are
visible; face and language jobs complete through the same durable protocol.

**DEPENDENCIES**
M0, PostgreSQL, the existing object-store connector and Qdrant adapters. Redis
is not required for durable job truth.

**BLOCKERS**
None for M1. A production outbox relay is intentionally deferred until an
external event consumer exists.

**ROLLBACK CONSIDERATIONS**
Stop producers and workers, drain or account for authoritative PostgreSQL jobs,
then revert worker/producer wiring. Redis legacy envelopes can be recovered
during the compatibility window. Downgrade `79b8c31f4d2a` only after exporting
or deliberately discarding processing history; downgrade deletes that schema.

## M2 — First-class media and local object storage — COMPLETE

**OBJECTIVE**
Represent source media with immutable identity, provenance and lineage, and
run the local stack against MinIO through an S3-compatible interface that AWS
can inherit without domain changes.

**CURRENT STATE**
`media.assets` holds one row per distinct byte sequence. Every arrival of an
asset is a separate `media.asset_sources` row, so deduplicating bytes does not
deduplicate provenance. `media.asset_derivatives` records lineage for later
modality phases, and `media.retention_holds` vetoes erasure. Media is
identified from its magic bytes rather than from the caller's declared type,
uploads are bounded while being read, and image headers are checked against a
pixel ceiling before any decoder allocates. `BlobStore` addresses objects by
bucket and key; the filesystem and S3 implementations pass one shared contract
suite, and Compose runs the S3 backend against MinIO.

**FILES CHANGED**
`app/core/config.py` (storage backend, S3 and media settings plus a startup
validator), `app/main.py` (blob store construction, readiness probe, bucket
creation, router), `app/api/v1/dependencies.py` (media service wiring),
`app/api/v1/enrolments.py` (bounded reads and magic-byte identification),
`app/domain/audit.py` (media actions), `app/domain/auth.py` (`media:read`,
`media:write`), `app/core/logging.py` (AWS SDK loggers held at WARNING),
`app/connectors/filesystem/__init__.py`, `pyproject.toml` (boto3, mypy
overrides), `docker-compose.yml` and `docker-compose.deploy.yml` (MinIO,
unpublished in deployments), `.github/workflows/ci.yml` (MinIO service, so CI
tests both storage backends), `.gitignore` (untracked identity data),
`.env.example`,
`frontend/src/lib/types.ts` and `frontend/src/app/settings/scope-picker.tsx`
(the new scopes are grantable).

**FILES CREATED**
`app/domain/storage.py`, `app/domain/content_types.py`, `app/domain/media.py`,
`app/connectors/filesystem/blob_store.py`, `app/connectors/s3/blob_store.py`,
`app/connectors/postgres/media_tables.py`, `app/connectors/postgres/media.py`,
`app/services/media.py`, `app/api/v1/media.py`, migration
`c3f7a91d8b40_media_assets.py`, tests `test_content_types.py`,
`test_blob_store.py`, `test_media_service.py`, `test_media_api.py`,
`test_media_repository.py`, and `docs/MEDIA_PIPELINE.md`.

**DATABASE CHANGES**
Revision `c3f7a91d8b40` creates the `media` schema with `assets`,
`asset_sources`, `asset_derivatives` and `retention_holds`. Constraints carry
the invariants: `sha256` is unique and format-checked, `status = 'erased'` and
`erased_at IS NOT NULL` must agree, a derivative cannot be its own source, and
provenance is unique per `(media, source type, system, external id)`. Downgrade
drops the schema.

**API CHANGES**
Adds `POST /api/v1/media`, `GET /api/v1/media`,
`GET /api/v1/media/{media_uuid}`, `GET /api/v1/media/{media_uuid}/sources`,
`GET /api/v1/media/{media_uuid}/content` (single-range capable),
`DELETE /api/v1/media/{media_uuid}`, and the retention-hold endpoints. Existing
enrolment, identification, language and processing contracts are unchanged;
enrolment still answers 422 for oversized and unrecognised images.

**AI/MODEL CHANGES**
None. No model, threshold or adapter was touched.

**BLACKGLASS INTEGRATION CHANGES**
None implemented. `SourceType.BLACKGLASS` and the external-source-id
deduplication key exist so M4 can attach without a schema change. No client,
credential seam or endpoint was added, and remote URL ingestion is deliberately
absent until M4 supplies the SSRF-safe downloader.

**SECURITY CONSIDERATIONS**
Closes three defects the audit identified: uploads were accepted on the
caller's declared content type, the size ceiling was applied only after the
whole body was buffered, and the API depended on a concrete filesystem store.
Adds decompression-bomb resistance, path-traversal refusal at the address type
and again at the filesystem backend, separate `media:read`/`media:write`
scopes where neither implies the other, admin-only erasure and holds, audited
reads of restricted and biometric assets, and `no-store`/`nosniff`/attachment
headers on content responses. Bytes are never served by content hash. MinIO is
loopback-only with its console disabled, and an S3 backend without credentials
fails at startup rather than on first upload.

**TESTS**
Backend regression with real PostgreSQL, Redis, Qdrant, MinIO, SCRFD and
AdaFace: **732 passed, 0 skipped**. The blob-store contract suite runs against
both backends. Migration rehearsal: upgrade to head, downgrade one, upgrade,
downgrade to base. Ruff, Ruff format and strict mypy clean. Frontend
typecheck, ESLint and 74 Vitest tests pass. Compose configuration validates.

**ACCEPTANCE CRITERIA**
Verified: identical bytes converge on one asset while every arrival is
retained; erasure removes bytes and keeps the metadata that makes a past
decision explicable; a retention hold blocks erasure and releasing it permits
erasure again; the filesystem and MinIO backends pass the same contract.
Partially met: derived artifacts have a lineage table and constraints but no
producer yet, because nothing derives media until M6.

**DEPENDENCIES**
M1. PostgreSQL, and either a writable directory or an S3-compatible endpoint.

**BLOCKERS**
None. Face and identification images remain on the legacy digest-addressed
store by choice; migrating live biometric objects is deferred to M3, where the
reference/observed split is introduced.

**ROLLBACK CONSIDERATIONS**
Set `FACEID_OBJECT_STORE_BACKEND=filesystem` to leave MinIO without touching
application code. The media router can be removed without affecting face,
language or processing paths. Downgrade `c3f7a91d8b40` only after exporting
media metadata: the object bytes survive in the bucket, but the rows saying
what they are and where they came from do not.

## M3 — Trust boundaries, authorization, and storage isolation

**Goal**
Make privacy and security boundaries explicit before multimodal expansion.

**Dependencies**
M1 and M2.

**Scope**

- Separate biometric and general Qdrant services, credentials, networks, backups, and connector settings.
- Introduce data classification and explicit reference/observed biometric roles.
- Extend authorization with action/resource/source/case constraints while retaining scopes as coarse grants.
- Add correlation IDs, audit export integrity metadata, secret-handling rules, and a localhost-only security profile.

**Out of scope**
Enterprise identity provider, cloud KMS implementation, or active testing outside the allowlist.

**Services/components affected**
API, BFF, workers, both Qdrant services, Postgres policy layer, Compose networks.

**Database changes**
Add policy/classification fields and access-decision audit details; migrate vector-location metadata to explicit domains.

**API changes**
Policy administration/read endpoints and consistent correlation identifiers; no broadening of browser proxy by default.

**Model/AI changes**
None.

**Security/privacy implications**
Reduces cross-domain exposure and accidental reference/observed biometric mixing. Default deny for sensitive classes.

**Tests required**
Authorization matrix, cross-domain credential/network denial, audit completeness, log redaction, deletion/retention, and Compose network tests.

**Acceptance criteria**
General credentials cannot query biometric vectors; observed biometrics cannot become reference biometrics without an audited authorized action; sensitive reads are attributable.

**Documentation required**
Threat model, data classification, trust-boundary diagram, policy matrix, incident-access runbook.

**Rollback plan**
Keep collection aliases and connector feature flags during vector migration; never recombine credentials after sensitive data is split.

## M4 — BlackGlass API integration foundation

**Goal**
Connect EagleEye and BlackGlass through a resilient, auditable API contract without shared persistence.

**Dependencies**
M1, M2, and the relevant M3 controls.

**Scope**

- Define versioned inbound/outbound contracts, scoped service credentials, external mappings, sync cursors, idempotency keys, delivery attempts, and operator visibility.
- Implement one narrow vertical slice: ingest a BlackGlass person/media reference or publish an EagleEye analysis result, selected from confirmed API documentation.
- Add timeout, bounded retry, circuit-breaking, rate-limit handling, replay protection, and contract tests against a local stub.

**Out of scope**
Database sharing, frontend merging, scraping undocumented endpoints, or importing all historical data in the first slice.

**Services/components affected**
Integration connector/service, processing jobs, API, audit, settings, integration administration UI.

**Database changes**
Create `integration.systems`, `integration.external_mappings`, `integration.sync_cursors`, and `integration.delivery_attempts`; secret values remain outside the database.

**API changes**
Versioned webhook/pull/push endpoints with signatures or scoped bearer auth, idempotency, pagination, and health/status.

**Model/AI changes**
None.

**Security/privacy implications**
Least-privilege service identity, credential rotation, signature/replay validation, payload minimization, explicit source/retention policy, and no remote active test without authorization.

**Tests required**
Local contract stub, schema compatibility, bad auth/signature/replay, duplicate event, pagination, rate limit, timeout, partial failure, retry exhaustion, deletion propagation, and audit tests.

**Acceptance criteria**
The selected slice is idempotent and recoverable; every external mapping/delivery is attributable; BlackGlass unavailability does not block core EagleEye operations.

**Documentation required**
API contract, credential setup, mapping rules, failure/replay runbook, data-flow/privacy assessment.

**Rollback plan**
Disable integration feature flag and stop delivery jobs; mappings and attempt history remain for audit/replay.

## M5 — Content, chunks, and multilingual retrieval

**Goal**
Turn the current Dhivehi search slice into a durable, citable content platform for Thaana, Romanized Dhivehi, English, and mixed text.

**Dependencies**
M1–M3.

**Scope**

- Promote documents/chunks with offsets, language/script spans, normalization/transliteration versions, source media linkage, metadata filters, delete/reindex, and citation-ready results.
- Build a versioned Dhivehi benchmark covering exact, transliterated, mixed, and semantic retrieval.
- Compare current E5 with BGE-M3 and lexical/hybrid approaches; adopt only the measured winner within resource budgets.
- Add hybrid search and optional reranking behind adapters.

**Out of scope**
LLM answer generation, writer identification, or claiming linguistic completeness from rule transliteration.

**Services/components affected**
Language API/worker, Postgres content tables, general Qdrant, frontend language/search workspace.

**Database changes**
Create/normalize `content.documents` and `content.content_chunks`; migrate existing `language_documents` through an idempotent backfill.

**API changes**
List/get/delete/reindex documents; search returns chunk text, offsets, score components, provenance, and filters.

**Model/AI changes**
Versioned embedding and reranker adapters; E5 stays available until benchmark evidence justifies change.

**Security/privacy implications**
Classification/ACL filters must be applied before result disclosure; raw content stays out of logs/vector payloads beyond the minimum needed for retrieval.

**Tests required**
Real Postgres/Qdrant ingestion/search, deletion/reindex, cross-script benchmark, filter authorization, duplicate content, crash recovery, and regression thresholds.

**Acceptance criteria**
Results cite immutable document/chunk offsets; cross-script benchmark meets recorded targets; reindex is lineage-preserving and recoverable; unauthorized chunks never surface.

**Documentation required**
Corpus card, transliteration limitations, embedding benchmark, index/reindex runbook, search-score semantics.

**Rollback plan**
Keep old language collection read path behind a feature flag; collections are versioned and old rows remain until validation.

## M6 — OCR and document analysis

**Goal**
Extract searchable, reviewable text and layout from local images/PDFs, with particular evaluation for Thaana.

**Dependencies**
M1, M2, M3, and M5.

**Scope**

- Add OCR adapter contract, page rendering, orientation/layout handling, regions/lines/tokens, confidence, and source coordinates.
- Benchmark local OCR candidates on a versioned Thaana/English/mixed dataset before selecting defaults.
- Feed approved OCR text into content/chunk search and provide side-by-side review/correction.

**Out of scope**
Handwriting attribution, remote OCR SaaS by default, or silently treating OCR output as ground truth.

**Services/components affected**
OCR worker, media/content schemas, general Qdrant, API, document review UI.

**Database changes**
Add `content.ocr_pages`, `content.ocr_regions`, corrections, and links to analysis runs/media assets.

**API changes**
Submit OCR, inspect status/pages/regions, correct text, accept/reject extraction, and search with source bounding boxes.

**Model/AI changes**
Local OCR/layout adapters with artifact digests and model cards; selection based on benchmark accuracy/latency/RAM.

**Security/privacy implications**
Sandbox parsers/renderers, enforce page/file limits, reject unsafe formats, isolate temp files, and keep document text access-controlled.

**Tests required**
Malformed PDF/image corpus, decompression bombs, Thaana benchmark, coordinates, rotation, mixed script, correction lineage, deletion, and worker recovery.

**Acceptance criteria**
Every OCR token maps to source coordinates and model/run; review corrections are preserved separately; benchmark and resource limits are published.

**Documentation required**
OCR model card, file-safety policy, benchmark report, operator review guide.

**Rollback plan**
Disable OCR adapter/version; preserve source media and prior outputs; rebuild search excluding the failed run.

## M7 — General image intelligence

**Goal**
Provide safe local image embeddings, tags/captions, similarity search, and evidence-linked observations.

**Dependencies**
M1–M3, M5, and M12 observation contract drafted before persistence freezes.

**Scope**

- Add SigLIP-or-benchmarked equivalent embeddings and an optional local VLM adapter for structured observations/captions.
- Generate thumbnails/derived crops, general-vector indexing, duplicate/similarity search, and review.
- Validate structured outputs against schemas and link every assertion to image region/model/run.

**Out of scope**
Face identity replacement, unrestricted free-form VLM claims, or cloud-only inference.

**Services/components affected**
Image worker, media service, general Qdrant, analysis records, API, review/search UI.

**Database changes**
Add image observations/regions through analysis schema and derived-asset links.

**API changes**
Image analysis submit/status/results, similar-image search, observation review/correction.

**Model/AI changes**
Image embedding adapter and optional VLM provider; local CPU/GPU resource profiles and pinned artifacts.

**Security/privacy implications**
Prompt/output injection handling for embedded text, strict structured output, access filters, and no automatic high-consequence claims.

**Tests required**
Determinism/provenance, malformed images, embedding retrieval benchmark, schema-invalid VLM output, hallucination review cases, deletion, and resource ceilings.

**Acceptance criteria**
Similar-image results are reproducible by model version; generated observations are clearly machine-produced, evidence-linked, and reviewable.

**Documentation required**
Model cards, prompt/version policy, evaluation set, limitations, GPU/CPU profiles.

**Rollback plan**
Disable model deployment and remove its versioned collection from active aliases; retain audit lineage.

## M8 — Vehicle intelligence

**Goal**
Detect, track, describe, search, and review vehicles without conflating uncertain observations with canonical entities.

**Dependencies**
M1–M3, M7, and M11 entity contracts.

**Scope**
Vehicle detections/crops, make/model/color/type/plate observations where lawful, embeddings, track aggregation, entity proposals, and human review.

**Out of scope**
Automatic enforcement decisions, unreviewed owner attribution, or jurisdiction-blind plate retention.

**Services/components affected**
Vehicle worker, image/video workers, entity/evidence services, general Qdrant, API/UI.

**Database changes**
Vehicle entity attributes remain observations/claims with provenance; add domain indexes and review decisions, not a parallel identity silo.

**API changes**
Vehicle search/filter/detail, observation review, merge/split proposals, media-linked sightings.

**Model/AI changes**
Detector/classifier/embedding/optional plate OCR adapters selected via dataset benchmarks.

**Security/privacy implications**
Plate data classification, retention policy, purpose limitation, access audit, and confidence disclosure.

**Tests required**
Vehicle benchmark, false-positive review, plate redaction/access, cross-camera tracks, merge/split, deletion, and load tests.

**Acceptance criteria**
No vehicle attribute becomes canonical without policy; sightings link to source/time/region/model; uncertain results enter review.

**Documentation required**
Dataset/model cards, plate policy, review guide, threshold calibration.

**Rollback plan**
Deactivate model/version and hide derived observations from active views; source media remains unchanged.

## M9 — Video intelligence

**Goal**
Process video incrementally into shots, frames, tracks, observations, and searchable evidence under bounded resources.

**Dependencies**
M1–M3 and relevant M7/M8/M11 contracts.

**Scope**
Local files first; metadata probe, safe decode, shot/keyframe extraction, multi-object tracking, face/vehicle/image observations, resumable processing, timeline UI, and optional later RTSP adapter.

**Out of scope**
Unbounded live surveillance, storing every decoded frame, or an RTSP production promise in the first slice.

**Services/components affected**
Video worker, media, processing, face/image/vehicle adapters, API, timeline UI.

**Database changes**
Add video technical metadata, shots, tracks, track observations, time ranges, and derived keyframe/crop links.

**API changes**
Upload/process/status/timeline/tracks/search; later camera-source/session endpoints behind explicit controls.

**Model/AI changes**
Decoder, detector, tracker, and optional action/scene adapters with per-stage provenance.

**Security/privacy implications**
Codec/parser isolation, duration/resolution/frame limits, camera credential secrecy, network-source SSRF controls, retention and access audit.

**Tests required**
Malformed/adversarial codecs, resume after crash, variable frame rate, track continuity, keyframe bounds, no-frame explosion, deletion, and CPU/RAM/GPU budgets.

**Acceptance criteria**
Long videos resume without restarting; every observation maps to time/source/model; derived artifacts stay within configured budgets.

**Documentation required**
Supported formats, resource sizing, camera threat model, retention, processing state machine.

**Rollback plan**
Stop video dispatch, deactivate adapter versions, and retain resumable job/source state.

## M10 — Audio intelligence

**Goal**
Provide local transcription, language/script-aware text, diarization, optional speaker similarity, and evidence-linked search.

**Dependencies**
M1–M3, M5, and M11/M12 contracts.

**Scope**
Audio extraction, Whisper-or-benchmarked STT, timestamps, language detection, diarization, optional speaker embeddings with separate biometric controls, transcript review, and content indexing.

**Out of scope**
Treating diarization labels as identities, enrolling observed voices automatically, or cloud transcription by default.

**Services/components affected**
Audio worker, media/content, biometric/general Qdrant as appropriate, API, transcript review UI.

**Database changes**
Add transcripts, timed segments, speakers-as-observations, corrections, and analysis lineage.

**API changes**
Transcribe/status/transcript/correct/search; explicit reviewed speaker-link endpoint if policy allows.

**Model/AI changes**
STT, diarization, and optional speaker-embedding adapters with local resource profiles.

**Security/privacy implications**
Voice embeddings are biometric and isolated; transcript classification/retention, consent/legal basis, access logging, and observed/reference separation apply.

**Tests required**
Multilingual/Dhivehi benchmark, timestamps, overlapping speakers, noise, malformed media, correction lineage, biometric isolation, deletion, and recovery.

**Acceptance criteria**
Transcript segments cite source time; diarization uncertainty is explicit; observed voices cannot enter reference index without reviewed authorization.

**Documentation required**
STT/diarization model cards, voice-biometric policy, benchmark, review guide.

**Rollback plan**
Deactivate model versions and rebuild content/vector indexes from immutable source audio.

## M11 — Universal entities and resolution

**Goal**
Represent people, organizations, vehicles, locations, accounts, documents, and other entities with source-scoped identifiers and reversible resolution.

**Dependencies**
M1–M3 and at least one non-face observation pipeline.

**Scope**
Universal entity types, external IDs, aliases, attributes-as-observations, deterministic/probabilistic match proposals, merge/split, canonical redirects, and review.

**Out of scope**
Replacing mature person tables in one migration or automatically merging from a single weak signal.

**Services/components affected**
Entity service, Postgres, analysis workers, API, people/entity UI, BlackGlass mappings.

**Database changes**
Create `core.entities`, `core.external_identifiers`, `core.entity_relationships`, and merge/split history; bridge current persons by stable mapping.

**API changes**
Entity CRUD/read, external-ID lookup, candidate resolution, merge/split, aliases, and relationship browsing.

**Model/AI changes**
Optional resolution scorer behind a versioned adapter; deterministic rules remain explainable inputs.

**Security/privacy implications**
Resolution can amplify privacy harm; require source/classification-aware policy, review for ambiguous merges, reversible history, and audited access.

**Tests required**
Source-scoped ID collisions, concurrent merges, split restoration, redirect cycles, authorization, deletion, BlackGlass mappings, and score explanations.

**Acceptance criteria**
External IDs never become internal keys; merges are reviewed/reversible; every resolution cites contributing observations and policy.

**Documentation required**
Entity model, resolution policy, merge/split runbook, compatibility mapping from persons.

**Rollback plan**
Disable universal reads and continue current person paths; bridge mappings preserve original person UUIDs.

## M12 — Evidence, claims, observations, analysis runs, and model registry

**Goal**
Create the shared provenance language that makes multimodal outputs explainable and reproducible.

**Dependencies**
M1–M3; coordinate contracts with M6–M11.

**Scope**
Analysis runs, typed observations, claims, evidence links, confidence semantics, human decisions/corrections, model artifacts/deployments/evaluations, and lineage queries.

**Out of scope**
Generic graph technology before relational access patterns prove need, or retroactively inventing provenance that was never recorded.

**Services/components affected**
All workers, Postgres, model adapters, API, analyst UI.

**Database changes**
Create `analysis.analysis_runs`, `analysis.observations`, `analysis.claims`, `analysis.evidence_links`, `analysis.model_registry`, and `analysis.model_deployments` with append/version semantics.

**API changes**
Lineage, evidence, claim review/correction, model registry/deployment/evaluation endpoints.

**Model/AI changes**
Adapters must resolve a registered model deployment and emit validated, versioned result envelopes.

**Security/privacy implications**
Evidence disclosure follows source classification; corrections never erase original machine output; model licenses/checksums and unsafe status are visible.

**Tests required**
Lineage completeness, immutable runs, claim/evidence authorization, correction history, artifact checksum, deployment rollback, deletion/retention, and cross-modal links.

**Acceptance criteria**
Any displayed machine claim can answer what source, region/time, model bytes, configuration, code/run, and review produced it.

**Documentation required**
Confidence vocabulary, lineage schema, model registry lifecycle, correction/audit semantics.

**Rollback plan**
Feature-flag new lineage reads while dual-writing from adapters; never delete recorded lineage during rollback.

## M13 — LLM understanding and evidence-grounded RAG

**Goal**
Use a local LLM to summarize, extract, answer, and assist analysis only from authorized, cited evidence.

**Dependencies**
M5, M11, M12, and the relevant modality pipelines.

**Scope**
Local LLM provider interface, retrieval policy, prompt/model/version registry, structured extraction, citation validation, answer abstention, prompt-injection defenses, evaluation, and analyst feedback.

**Out of scope**
Autonomous consequential decisions, uncited factual claims, internet-dependent inference by default, or model fine-tuning before evaluation justifies it.

**Services/components affected**
LLM worker/service, content search, evidence service, API, analyst workspace.

**Database changes**
Analysis runs/claims/evidence store prompts by digest/template version and policy-safe outputs; do not store hidden chain-of-thought.

**API changes**
Evidence-grounded query, summarize/extract jobs, cited response, feedback/review, model selection for authorized admins.

**Model/AI changes**
Local LLM adapter; candidate selection based on multilingual/Dhivehi quality, structured output, RAM/VRAM, latency, and license.

**Security/privacy implications**
ACL-aware retrieval, prompt injection and data exfiltration tests, output schema validation, content minimization, no secrets in prompts/logs, and explicit human review.

**Tests required**
Golden QA/extraction sets, citation entailment, unsupported-answer abstention, injection corpus, cross-ACL leakage, malformed output, resource ceilings, and regression evaluation.

**Acceptance criteria**
Every substantive answer sentence cites accessible evidence or is marked uncertain; unauthorized content cannot influence or appear in output; model/prompt/run are reproducible.

**Documentation required**
LLM model card, evaluation report, prompt/version policy, injection threat model, analyst limitations.

**Rollback plan**
Disable the active model deployment and preserve retrieval/search without generation.

## M14 — Events, narratives, universal search, and relevance ranking

**Goal**
Organize evidence into time-bounded events and reviewable narratives, with one access-controlled search experience.

**Dependencies**
M5, M11–M13.

**Scope**
Event records, participants/locations/time, narrative versions, saved searches, faceted universal retrieval, ranking fusion, deduplication, and evidence timelines.

**Out of scope**
Presenting generated narratives as established fact or creating events without traceable observations.

**Services/components affected**
Event/search services, Postgres, general Qdrant, LLM worker, API, analyst UI.

**Database changes**
Add events, event-entity/media/claim links, narrative versions, saved queries, and ranking/evaluation metadata.

**API changes**
Universal search, event CRUD/review, timeline, narrative generate/revise/approve, saved search.

**Model/AI changes**
Ranking fusion/reranker and optional LLM narrative generation, all benchmarked/versioned.

**Security/privacy implications**
Search/ranking must apply ACLs before retrieval and aggregation; narratives preserve uncertainty, dissent, corrections, and evidence links.

**Tests required**
Cross-modal ranking benchmark, ACL leakage, temporal ordering, dedupe, narrative citation/contradiction, revision history, and pagination stability.

**Acceptance criteria**
Universal results are explainably ranked and source-cited; narratives are versioned, reviewable, and never erase contrary evidence.

**Documentation required**
Ranking model, event semantics, narrative policy, evaluation and review guide.

**Rollback plan**
Disable fusion/generation and retain modality-specific search plus immutable event/evidence records.

## M15 — Analyst workflows and explainability

**Goal**
Give authorized analysts a coherent workflow for triage, review, correction, evidence comparison, and defensible export.

**Dependencies**
M11–M14.

**Scope**
Unified review inbox, filters/assignment, entity merge/split, media timelines, side-by-side evidence, model/run detail, corrections, audit views, and export manifests.

**Out of scope**
Replacing BlackGlass UI or adding actions that bypass domain APIs.

**Services/components affected**
Next.js BFF/UI, review/entity/evidence/search APIs, audit.

**Database changes**
Review tasks, assignments, dispositions, comments, and export manifests where durable collaboration is required.

**API changes**
Unified review/task endpoints, explainability bundles, export requests/status/download.

**Model/AI changes**
No new model required; expose model cards, scores, provenance, and limitations consistently.

**Security/privacy implications**
Case/resource authorization, sensitive-view audit, export watermark/manifest/expiry, reauthentication for destructive actions, and no biometric browser persistence.

**Tests required**
Accessibility, keyboard/RTL Thaana, authorization, concurrent review, stale decisions, export integrity, no-cache sensitive responses, and browser E2E.

**Acceptance criteria**
An analyst can trace and correct any result without raw database access; every review/export is attributable and policy-checked.

**Documentation required**
Analyst guide, explainability glossary, export policy, accessibility statement.

**Rollback plan**
Keep modality-specific screens and APIs available; disable unified workflow features without losing review records.

## M16 — Security verification and hardening

**Goal**
Make security testing repeatable, authorized, CI-visible, and tied to remediation evidence.

**Dependencies**
M3 and stable API/media/integration surfaces.

**Scope**

- Threat-model refresh and explicit local test allowlist.
- SAST, dependency, secret, container, SBOM/license, IaC/config scans.
- Auth/authz/rate-limit/audit/logging regression suites.
- File parser/upload/SSRF/path traversal/decompression-bomb checks.
- Local Docker DAST with a documented API/web scanner profile.
- Findings register with severity, owner, evidence, due date, exception approval, and retest.

**Out of scope**
Scanning `cyber-ai`, BlackGlass, GitHub, or any third-party address without explicit authorization; destructive denial-of-service testing.

**Services/components affected**
CI, local Compose security profile, all public APIs, BFF, containers, documentation.

**Database changes**
None required; security findings should not be mixed into application production tables.

**API changes**
Only remediations and security headers/error consistency identified by tests.

**Model/AI changes**
Add model artifact/license scanning and prompt/model security cases where applicable.

**Security/privacy implications**
Test fixtures contain synthetic data; scanner logs are scrubbed; credentials are short-lived/local; scan targets are fail-closed against the allowlist.

**Tests required**
Semgrep/Bandit or justified equivalents, pip/npm audits with policy, Gitleaks, Trivy image/config, Checkov, SBOM, localhost DAST, authorization matrix, and parser/SSRF suites.

**Acceptance criteria**
No open unaccepted critical/high findings; every exception is time-bound and documented; CI blocks regressions; local DAST report is reproducible.

**Documentation required**
Threat model, security test plan, allowlist, findings/remediation report, incident and secret-rotation runbooks.

**Rollback plan**
Security fixes are reverted only with documented risk acceptance; scanner rollout may begin warning-only before an agreed enforcement date.

## M17 — Performance, observability, backup, and disaster recovery

**Goal**
Prove the local platform meets resource, latency, throughput, recovery, and operational visibility targets.

**Dependencies**
M1, M2, M3, and representative workloads from later pipelines.

**Scope**
Correlation/tracing, Prometheus-compatible metrics, dashboards/alerts, workload budgets, backpressure, load/soak tests, PostgreSQL/MinIO/Qdrant/Redis backup strategy, restore drills, RPO/RTO, and graceful degradation.

**Out of scope**
Optimizing unmeasured code, exceeding safe host resource ceilings, or treating Redis backup as job truth.

**Services/components affected**
All services, Compose, monitoring stack, backup jobs, operator UI/docs.

**Database changes**
Only operational metadata if needed; no high-cardinality metrics in application tables.

**API changes**
Internal metrics/health detail and operator-safe status; sensitive internals remain authenticated/private.

**Model/AI changes**
Per-model CPU/RAM/VRAM/concurrency profiles, batching, warm-up, and fallback behavior.

**Security/privacy implications**
Metrics/traces exclude content and biometric payloads; backups are encrypted/access-controlled and deletion implications documented.

**Tests required**
Load/soak/fault injection, worker kill/recovery, dependency outage, disk pressure, backup consistency, bare restore, and RPO/RTO measurement.

**Acceptance criteria**
Published SLOs/resource budgets hold under representative load; restore drill meets recorded RPO/RTO; alerts detect queue age, failures, capacity, and dependency loss.

**Documentation required**
SLOs, capacity model, dashboards/alerts, backup/restore and disaster-recovery runbooks.

**Rollback plan**
Observability is additive; performance changes use flags and retain known-safe worker/concurrency settings.

## M18 — AWS deployment readiness

**Goal**
Deploy EagleEye to AWS without changing domain contracts or abandoning local-first operation.

**Dependencies**
M2, M3, M16, M17, and a stable release candidate.

**Scope**
Document/test adapters and mappings for S3, managed PostgreSQL, ElastiCache-compatible Redis, private vector deployment, secret/KMS, container runtime, networking, logs/metrics, backup, autoscaling, and migration/cutover.

**Out of scope**
Making AWS mandatory for development, selecting services without cost/security review, or automatic production deployment in the first slice.

**Services/components affected**
Deployment/IaC, storage/queue settings, CI/CD, secrets, observability, runbooks.

**Database changes**
None beyond portability fixes identified by staging migration rehearsals.

**API changes**
None expected; environment-specific base URLs/identity integration remain configuration.

**Model/AI changes**
Artifact distribution and CPU/GPU runtime profiles; local adapters/contracts remain unchanged.

**Security/privacy implications**
Private networking, least-privilege IAM, KMS encryption, WAF/rate limiting, secret rotation, data residency, biometric classification, and audited administrative access.

**Tests required**
IaC lint/scan, ephemeral staging deploy, migration rehearsal, contract tests against AWS adapters, failover, backup/restore, load, and cost-budget alarms.

**Acceptance criteria**
The same application tests pass locally and in AWS staging; no public data services; recovery/security/cost controls are documented and exercised.

**Documentation required**
AWS reference architecture, service mapping/alternatives, IAM/data-flow diagrams, deployment/cutover/rollback/cost runbooks.

**Rollback plan**
Blue/green or versioned deployment rollback; retain local deployment and exportable backups until AWS cutover is verified.

## Phase governance

At the start of each phase, confirm dependencies and restate scope. During the phase, do not absorb attractive adjacent work unless it is required for acceptance. At phase completion, report:

- phase implemented;
- files changed;
- migrations added;
- API endpoints added/changed;
- models/adapters added/changed;
- tests run and exact results;
- security checks run and findings;
- documentation updated;
- known limitations;
- rollback procedure;
- next recommended phase.

A phase is complete only after its acceptance criteria have evidence. A passing unit suite cannot substitute for a required integration, benchmark, security, or restore test. If a criterion cannot be run in the current environment, the phase stays partial and the missing evidence is named.
