# EagleEye — what is missing

Generated: 2026-08-31 · Commit: `7c9675d` + uncommitted M2 work · Migration head: `c3f7a91d8b40`

The gap register between the 160-section Codex Master Prompt and the repository.
Section numbers (`§n`) reference the prompt. Phase labels (`M3`–`M18`) reference
`MASTER_IMPLEMENTATION_PLAN.md`.

Classification per §145: `MISSING` (nothing exists), `PARTIAL` (a useful slice
exists, production requirements absent), `NOT VERIFIED` (exists, unproven).

Companion document: `PROGRESS.md`.

---

## Summary

**16 of 19 phases remain.** The three largest structural absences, in the order
they block each other:

1. **The evidence model does not exist** (§50–§58). §158 calls the
   OBSERVED / CLAIMED / INFERRED / CORROBORATED / VERIFIED / CONTRADICTED
   distinction more important than any individual model, and none of it is
   built. Every intelligence capability depends on it.
2. **There is no universal entity model** (§16). Only `persons` exists. No
   organization, vehicle, location, account or event can be represented.
3. **Biometric and general vectors share one Qdrant service** (§11). The
   separation the prompt calls mandatory is not yet physical.

Everything else is downstream of those three.

---

## 1. Blocking gaps — nothing above them can be built correctly

### 1.1 Evidence, claims, observations — §50–§58 · MISSING · M12

No `intelligence.claims`, `intelligence.evidence`, `graph.relationships`,
`graph.relationship_evidence`, no truth-state vocabulary, no conflict
detection, no `intelligence.analysis_runs`.

Consequences today:

- §51 — "This is Ahmed's vehicle" cannot be recorded as a claim distinct from a
  registry fact.
- §54 — no client-side status elevation is *possible*, but only because the
  states do not exist. This is absence, not a control.
- §57 — a registry relationship and a contradicting social claim cannot
  coexist, so conflicts cannot be detected.
- §72 — AI analysis cannot be versioned; there is no run to version.
- §97 — "why?" cannot be answered for anything but a face decision.

**Design note:** the observation contract must be drafted *before* M6–M10
persist anything, or each modality invents its own shape and M12 becomes a
migration of five incompatible schemas. The plan already sequences this.

### 1.2 Universal entity model — §16, §17, §18 · MISSING · M11

`core.entities` does not exist. `core.external_identifiers` does not exist —
`person_external_identifiers` is person-specific and cannot carry a vehicle
registration, company number or BlackGlass UUID.

None of the 16 entity types in §16 are representable except PERSON. UUIDv7
(§17) is not used for high-ingestion tables.

The existing `persons` table is mature and must be **bridged, not replaced**
(§4, §147). Migrating it in one step is the failure mode to avoid.

### 1.3 Biometric vector isolation — §11, §102, §153 · MISSING · M3

One Qdrant service holds face embeddings and language embeddings. They share
credentials, network, failure domain and backup policy.

§11 requires `qdrant-general` and `qdrant-biometric` as separate services, and
states that generic workers must not automatically receive biometric
credentials. Today the language worker holds credentials that reach the
biometric collections.

**Risk note for whoever does this:** splitting means re-indexing live face
embeddings into a new service. Do it dual-write behind a feature flag with a
rehearsed rollback, not as a cutover.

### 1.4 Reference vs observed faces — §26, §154 · MISSING · M3

Collections are named by model provenance (`face_embeddings__<model>__…`), not
by role. `face_reference_adaface_v1` and `face_observation_adaface_v1` do not
exist as distinct spaces.

Every enrolled face is currently treated as a reference face. There is no
mechanism to hold an observed face — one extracted from collected media — in a
separate space, and therefore no mechanism to prevent the auto-promotion §154
forbids. Nothing promotes observed faces today because observed faces do not
exist yet; the control still has to be built before M7/M9 create them.

### 1.5 Trusted identity source — §25 · MISSING · M3

No `IdentitySource` interface, no `LocalIdentitySource`. Reference faces
currently arrive by operator upload. `SourceType.IDENTITY_SERVER` exists in the
media provenance enum as a placeholder; nothing produces it.

---

## 2. Security gaps

### 2.1 SSRF protection — §23, §24 · MISSING · M4

No safe downloader exists, and **no remote URL ingestion exists either** — this
is deliberate, not an oversight. Adding a generic HTTP fetch before the
downloader would create exactly the vulnerability §24 exists to prevent.

Required before any collector or BlackGlass URL is fetched: DNS/IP validation,
re-validation after **every** redirect, the full private/loopback/link-local/
metadata blocklist, size and type limits, magic-byte check, and automated SSRF
tests.

### 2.2 Security tooling — §107, §108, §109, §110, §111, §137 · MISSING · M16

Verified absent on this machine: `bandit`, `pip-audit`, `semgrep`, `gitleaks`,
`trivy`, `checkov`. No OWASP ZAP baseline. No SBOM. No image signing.

**No dependency, SAST, secret, container or IaC scan has ever been run on this
repository.** Ruff's `S` rules (flake8-bandit) pass and give partial SAST
coverage, but that is not a substitute.

The §137 security gate cannot be satisfied today.

### 2.3 Authorization depth — §92, §93, §94 · PARTIAL · M3

Seven scopes exist and are enforced backend-side. What is missing:

- Roles as a concept (§92: ADMIN / OPERATOR / REVIEWER / AUDITOR / SERVICE) —
  scopes are granted directly, with no role layer.
- ABAC dimensions (§93): unit, case, classification, purpose.
- Named biometric privileges (§94): `FACE_SEARCH`, `REFERENCE_FACE_VIEW`,
  `FACE_ENROLL`, `FACE_REVIEW`, `FACE_EXPORT`, `BIOMETRIC_ADMIN`. Today
  `review` and `identify` cover this ground too coarsely.
- OIDC/SSO compatibility.

### 2.4 Docker network segmentation — §99, §153 · MISSING · M3

One `faceid` bridge network carries everything. §99 requires conceptually
separate frontend / backend / processing / data / biometric networks, so that a
generic text worker *cannot route to* biometric Qdrant rather than merely
being trusted not to.

### 2.5 PostgreSQL role separation — §101 · PARTIAL · M3

One application role does migrations and serving. §101 requires separate
migration, application and read-only analytics roles.

### 2.6 MinIO credential separation — §103 · PARTIAL · M3

Four buckets exist with correct separation, but a single root credential
reaches all of them. §103 requires per-bucket credentials so generic NLP
workers cannot read reference-face buckets.

### 2.7 Rate limiting coverage — §105 · PARTIAL · M16

Covered: sign-in, identify, enrol. Not covered: semantic search, image search,
media upload, exports, LLM analysis, video processing, BlackGlass requests.
Server-side maximums exist for page size; `top_k`, video duration, batch size
and frame count have no ceilings because those features do not exist yet.

### 2.8 Business-logic security tests — §113 · MISSING · M16

No tests attempt to view a reference face without permission, export raw
embeddings, bulk-enumerate identities, bypass the decision workflow, elevate a
CLAIM to an AUTHORITATIVE RECORD, or overwrite `REGISTERED_TO` with a social
claim. Several of these cannot be written until §1.1 and §1.4 exist.

### 2.9 Other security absences

| Gap | § | Phase |
| --- | --- | --- |
| No TLS or reverse proxy; deployment relies on a WireGuard overlay | — | M16 |
| No MFA, password reset, session inventory, or reauth for destructive actions | §92 | M16 |
| No request/correlation IDs — verified absent from the codebase | §95, §122 | M3 |
| No audit export or integrity/tamper-evidence mechanism | §95 | M3 |
| No `security-test` Compose profile | §106 | M16 |

---

## 3. Missing AI capabilities

None of the following exist: no adapter, no model weights, no worker, no
tables, no API, no UI. Listed in dependency order.

| Capability | § | Phase | Notes |
| --- | --- | --- | --- |
| Content abstraction (`core.content_items`, `content_chunks`) | §43, §44 | M5 | `language_documents` is the seed; needs offsets and citations |
| BGE-M3 evaluation vs current E5 | §45, §46 | M5 | Requires a versioned Dhivehi benchmark first — §46 forbids assuming |
| Hybrid search and reranking | §86 | M5 | |
| OCR | §36 | M6 | Thaana evaluation required before selecting a model |
| Image embeddings (SigLIP 2) | §35 | M7 | |
| Vision-language model | §34, §49 | M7 | |
| Object detection | §34 | M7 | |
| Conditional AI routing | §80 | M7 | Currently moot — one pipeline. Required before scale |
| Vehicle intelligence | §37 | M8 | |
| Vehicle re-identification | §38 | post-M8 | Explicitly NOT IMPLEMENTED, per §38 |
| Video intelligence | §39 | M9 | |
| Speech-to-text | §40, §41 | M10 | |
| Speaker diarization | §42 | M10 | |
| Speaker embeddings | §40 | M10 | Biometric — needs §1.3 isolation first |
| Local LLM layer | §47, §48 | M13 | |
| Prompt-injection defences | §87 | M13 | |
| LLM tool allowlists | §88 | M13 | |
| RAG authorization | §85 | M13 | Filters must apply *at retrieval*, not after |
| Entity resolution | §59, §60 | M11 | |
| Intelligence NLP: political/geopolitical/economic/security/social | §61–§66 | M14 | |
| Sentiment (overall / target / speaker) | §67 | M14 | Single-label sentiment is explicitly forbidden |
| Stance | §68 | M14 | |
| Narrative intelligence | §69 | M14 | |
| Event intelligence | §70 | M14 | |
| Risk analysis with rationale | §71 | M14 | Bare `HIGH` is forbidden |
| Universal search | §84 | M14 | |
| Model registry (`system.model_versions`) | §73 | M12 | Per-result model strings exist; no queryable registry |
| Face calibration tooling (ROC/FAR/FRR) | §33 | post-M12 | See §5 below |

---

## 4. Infrastructure and operations

### 4.1 Compose topology — §5, §152 · PARTIAL · M3+

Present (8): `api`, `frontend`, `worker`, `language-worker`, `postgres`,
`redis`, `qdrant`, `minio`.

Absent: `qdrant-general` + `qdrant-biometric` split, and the specialist workers
`worker-vision`, `worker-ocr`, `worker-video`, `worker-audio`, `worker-text`,
`worker-entity-resolution`, `worker-intelligence`. Optional `prometheus` and
`grafana` are also absent.

Workers should be added with the pipelines that need them, not in advance.

### 4.2 Observability — §120, §121, §122 · PARTIAL · M17

- `/health` and `/readyz` exist and are correct.
- **No `/metrics` endpoint.** `/system/metrics` returns an authenticated JSON
  snapshot; it is not Prometheus-compatible and carries no time series.
- No correlation IDs — verified absent.
- No alerting, no dashboards, no tracing.
- Of the 16 measurements §120 requires, only queue depth and job latency are
  available.

### 4.3 Backup and disaster recovery — §115 · MISSING · M17

No backup scripts for PostgreSQL, Qdrant or MinIO. No restore procedure. No
RPO/RTO. §115: *a backup never restored is not validated* — nothing has been
backed up, so nothing has been restored.

`scripts/` contains `deploy.sh` and import/purge utilities only.

### 4.4 Data deletion and lifecycle — §116, §117 · PARTIAL · M17

Person erasure works across PostgreSQL, Qdrant and object storage, and media
erasure works with hold checks. Missing: lifecycle classes for temporary
artifacts (aligned crops, debug images, OCR intermediates, video frames), and
hot/warm/archive/delete tiering.

### 4.5 Scale and partitioning — §118, §119 · MISSING · M17

No time partitioning on `media.assets`, `media.asset_sources`,
`processing.jobs` or `audit_events`. No documented partition strategy. The 1M
images/month target of §118 has not been load-tested — no load or soak test
exists.

---

## 5. Calibration — §31, §32, §33 · PARTIAL

This one is worth stating plainly because it is easy to misread as complete.

**Implemented and correct:** thresholds are configuration with no code
defaults; the app refuses to start without them; every decision records the
policy version that produced it; similarity is never converted into a fake
probability (§27).

**Missing:**

- Decision states. `DecisionOutcome` is `accept` / `review` / `reject`. §31
  requires `MATCH`, `REVIEW_REQUIRED`, `NO_MATCH`, `LOW_QUALITY`,
  `MULTIPLE_FACES` after calibration, and `CANDIDATES_ONLY` before it.
- Quality signals (§32). No face size, blur, pose, occlusion or illumination
  measurement — so `LOW_QUALITY` cannot be produced.
- Calibration tooling (§33). No genuine/impostor pair generation, no ROC, FAR,
  FRR, threshold analysis, review-band analysis, or population-specific
  evaluation.

**The operating thresholds in use are illustrative, not validated.** `.env`
says so. Until §33 tooling runs against representative data, no false-match
rate claim can be made about this deployment.

---

## 6. BlackGlass integration — §7, §19, §89, §90, §91, §114, §125 · MISSING · M4

Nothing exists: no client, no credential seam, no `integration.systems`, no
`integration.blackglass_objects` mapping, no sync cursors, no delivery
attempts, no webhook or pull endpoints, no event contracts, no contract tests,
no integration UI.

What M2 deliberately prepared:

- `SourceType.BLACKGLASS` in the media provenance enum.
- `external_source_id` as part of the provenance uniqueness key, so redelivery
  is idempotent (§91) without a schema change.
- `processing.outbox_events` as the durable event boundary (§90).

Correctly observed so far: no cross-database dependency exists in either
direction (§7), and BlackGlass holds no EagleEye credentials (§114).

---

## 7. Documentation — §140 · PARTIAL

7 of 21 documents exist.

Present: `ARCHITECTURE.md`, `IMPLEMENTATION_STATUS.md`,
`MASTER_IMPLEMENTATION_PLAN.md`, `MEDIA_PIPELINE.md`, `PROCESSING_JOBS.md`,
`REPOSITORY_AUDIT.md`, `API_EXAMPLES.md`, plus this file and `PROGRESS.md`.

Missing: `DATA_MODEL.md`, `SECURITY.md`, `SECURITY_TEST_PLAN.md` (§139),
`FACE_ID.md`, `VIDEO_PIPELINE.md`, `AUDIO_PIPELINE.md`,
`VEHICLE_INTELLIGENCE.md`, `OCR.md`, `ENTITY_RESOLUTION.md`,
`EVIDENCE_MODEL.md`, `INTELLIGENCE_ANALYSIS.md`, `MODEL_MANAGEMENT.md`,
`BLACKGLASS_INTEGRATION.md`, `LOCAL_DEPLOYMENT.md`, `BACKUP_RECOVERY.md`,
`PERFORMANCE.md`, `AWS_MIGRATION.md`.

Most should be written **with** their phase rather than in advance — §140
forbids documenting planned functionality as implemented. Three are worth
writing sooner because they govern work rather than describe it:
`SECURITY_TEST_PLAN.md`, `EVIDENCE_MODEL.md` and `AWS_MIGRATION.md`.

---

## 8. Frontend — §124 · PARTIAL · M15

Present (15 routes): dashboard, identify, enrollments, persons, person detail,
review, review detail, matches, match detail, audit, settings, language,
tracker, sign-in, root.

Absent from the §124 target: Media Intelligence (images/videos/audio/
documents), Image Intelligence, Vehicle Intelligence, OCR Intelligence, Audio/
Speech Intelligence, Entities, Relationships, Claims, Observations, Evidence,
Events, Narratives, the whole News/Social analysis group, AI Analysis, Model
Registry, Processing Jobs, Alerts/Triggers, and Integrations.

Correctly observed: no screen fabricates data for an unimplemented backend
(§124), and the browser reaches only the EagleEye API through a server-side
proxy (§123).

**Note:** the `/tracker` route is *not* a production tracking system. It gates
frames by motion in the browser and submits them for face identification. There
are no camera-source records, no RTSP ingestion, no multi-object tracking, no
stable track IDs and no persistent sightings.

---

## 9. AWS readiness — §126–§129 · PARTIAL · M18

**Done, and it is the substantive part:** the storage seam. `BlobStore` has
filesystem and S3 implementations proven against one contract suite, so
MinIO → S3 is a configuration change. The queue is PostgreSQL-authoritative, so
it does not need SQS to be correct.

**Missing:** `docs/AWS_MIGRATION.md` (§126), the account model (§127), the
security requirements list (§128), biometric isolation design (§129), any IaC,
and a `SqsJobQueue` implementation (§13).

---

## 10. Untracked and unresolved

| Item | State |
| --- | --- |
| `CSV/people.csv` | 21 MB of real identity records including national ID numbers. Now git-ignored. **Not backed up anywhere.** |
| `docker-compose.import.yml` | Untracked, ownership unresolved |
| `scripts/import_dheni.py` | Untracked, ownership unresolved |
| `scripts/purge_people.py` | Untracked, ownership unresolved |
| `.venv/bin/pip` | Stale `/home/axmyn/Projects/AI` shebang — use `python -m pip` |

---

## Recommended order

The plan's dependency order, with the reasoning made explicit:

1. **M3** — trust boundaries. Splitting Qdrant and defining reference vs
   observed gets harder with every additional vector written. Do it before
   M7/M9/M10 create observed biometrics.
2. **M4** — BlackGlass foundation, including the SSRF-safe downloader that
   every later collector depends on.
3. **M5 → M6** — content platform, then OCR as the first new modality through
   the M1/M2 contracts.
4. **M7–M10** — image, vehicle, video, audio, with the M12 observation contract
   drafted first so five schemas do not diverge.
5. **M11 → M12** — entities, then evidence and model registry.
6. **M13 → M14 → M15** — LLM/RAG, events and search, analyst workflow.
7. **M16 → M17 → M18** — security verification, performance and DR, AWS.

M16 is last by dependency, not by priority. The absence of any dependency,
secret or container scan (§2.2) is worth addressing opportunistically rather
than waiting for its phase — installing the tools and recording a baseline is
hours of work, not a phase.
