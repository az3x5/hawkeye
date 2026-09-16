# BlackGlass evidence workflow

This workflow handles authorized text/documents, OCR, transcription, translation,
sampled visual observations, semantic indexing, and cited summaries. It does not
perform person identification, ownership inference, plate lookup, or cross-stream
person tracking. Existing face enrollment is a separate pipeline and is not
started by any endpoint or worker described here.

## Original evidence storage

The current deployment stores uploaded originals on the Cyber-AI PC. The API
and evidence workers share the persistent Docker volume mounted at
`/srv/objects`; set `OBJECT_STORE_BACKEND=filesystem` (the Compose default).
PostgreSQL holds structured records, extraction provenance and queue state.
Qdrant holds searchable vectors. An embedding is not an archive of its source,
so the checksum-addressed original remains on Cyber-AI for citation review.

BlackGlass normally sends text and attachments together to `/evidence/ingest`.
The older `/evidence/text` and `/evidence/media` routes remain available for
single-part clients. BlackGlass must not send local paths: file bytes cross the
authenticated API connection and are committed to Cyber-AI before their item is
acknowledged. Back up the `object-data` volume together with PostgreSQL;
restoring only the database would leave report citations without originals.

The `/evidence/objects` manifest endpoints are optional future adapters for an
operator-configured shared S3 source. They are not part of the current Cyber-AI
flow and reject submissions when that source is not configured. Enabling them
later is a deliberate storage migration; it is not required for local upload.

## APIs implemented in this change

All paths begin `/api/v1/integrations/blackglass`.

| Method/path | Permission | Purpose |
| --- | --- | --- |
| POST `/evidence/ingest` | `language` + `media:write` | Unified text and multi-file BlackGlass ingestion |
| POST `/evidence/text` | `language` | Preserve text; enqueue evidence analysis |
| POST `/evidence/media` | `media:write` | Multipart original upload for manual input |
| POST `/evidence/objects` | `media:write` | Optional future shared-object adapter; disabled when unconfigured |
| POST `/evidence/objects/batch` | `media:write` | Optional future shared-object batch adapter |
| GET `/evidence/{analysis_id}` | `media:read` | Source, processing state, excerpts, findings |
| GET `/evidence/{analysis_id}/report` | `media:read` | BlackGlass schema-2 report JSON |
| GET `/evidence/{analysis_id}/content` | `media:read` | Checksum-verified original download |
| GET `/evidence/events?cursor=0&limit=50` | `media:read` | Replay result events |
| GET `/evidence/status` | `media:read` | Live worker counts and caller's backlog |
| GET `/evidence-search?q=...&limit=10` | `media:read` | Lexical + semantic evidence retrieval |

The authenticated token **subject** owns each analysis. Read access requires
both the scope and the same subject. A UUID or asserted `attributes.source_system` does not
grant access to another account's content. Use a stable BlackGlass service
subject across token rotation and issue it the required scopes. No automatic
administrator cross-owner bypass is provided by these new endpoints.

### Direct file upload to Cyber-AI

The preferred BlackGlass endpoint is:

```http
POST /api/v1/integrations/blackglass/evidence/ingest
Authorization: Bearer <EagleEye service token>
Idempotency-Key: <stable BlackGlass request ID>
Content-Type: multipart/form-data
```

The form has a required `metadata` JSON string and zero to twenty `files`
parts. The metadata must include `report_request_id`, `subject`, `source_id`, and `source_type`;
it may include `text`. At least text or one file is required. One request may
carry both a post body and its attachments:

```json
{
  "schema_version": "1.1",
  "report_request_id": "BG-REPORT-123",
  "subject": {
    "subject_type": "social_profile",
    "subject_id": "BG-PROFILE-456",
    "display_label": "Profile under review"
  },
  "source_id": "POST-1001",
  "source_type": "post",
  "attributes": {
    "source_system": "blackglass-prod",
    "source_url": "https://blackglass.example/posts/POST-1001",
    "collected_at": "2026-09-15T08:00:00Z"
  },
  "text": "Original post text",
  "options": {
    "language": "mixed",
    "translate_to": "en",
    "summarize": true
  },
  "batch_id": "BG-REPORT-123-BATCH-1",
  "batch_sequence": 1,
  "final_batch": false
}
```

`source_id` and `source_type` are indexed PostgreSQL columns used for exact lookup and
deduplication. Optional collector details belong in `attributes`; clients must not send a nested
`source` object in new integrations. Schema 1.0 nested requests remain accepted temporarily and
are normalized to the flat schema before persistence.

The 202 response contains an `items` array: one analysis acknowledgement for
the text and one for each file. All items retain the same report request,
subject, source and batch correlation. Exact replays converge on the same
analysis IDs even if the HTTP request ID changes; the idempotency header is
also stored as transport provenance. Per-file limit is 50 MiB, combined-file
limit is 100 MiB. Earlier parts can already be committed if a later part is
invalid or too large, so retry the complete request: committed items converge
on their existing analysis IDs. Larger video must be sent as finite ordered
segments.

Send `multipart/form-data` to `/evidence/media` with:

* `file`: the original image, audio, video or document bytes;
* `metadata`: a JSON string containing the same `schema_version`, `source_id`, `source_type`,
  `attributes`,
  `options`, and optional `stream` fields used by text ingestion.

The acknowledgement is returned only after the original is durably stored and
the database job is committed. The worker reads the same checksum-addressed
file from the persistent Cyber-AI volume. Replaying the same source and bytes is
idempotent.

### Optional shared original manifest (not used now)

```json
{
  "schema_version": "1.1",
  "source_id": "BG-VIDEO-123",
  "source_type": "video",
  "attributes": {
    "source_system": "blackglass-prod",
    "collected_at": "2026-09-14T10:00:00Z"
  },
  "object": {
    "key": "blackglass/videos/example.mp4",
    "version_id": "<optional-actual-S3-version-id>",
    "sha256": "<64-lowercase-hex-characters>",
    "byte_size": 123456,
    "mime_type": "video/mp4"
  },
  "options": {
    "language": "en",
    "summarize": true,
    "max_seconds": 120,
    "max_frames": 6,
    "max_pages": 20
  }
}
```

Use real values; placeholders above are not valid input. Bulk input is
`{"items": [manifest, manifest]}` with 1–50 items. Each object is HEAD-checked
before acceptance, and bytes are only fetched later by the worker. File limit:
50 MiB per object. Split larger source recordings into retained finite segments.
For each segment, optionally include:

```json
{
  "stream": {
    "stream_id": "stream-1",
    "segment_id": "segment-42",
    "sequence": 42,
    "started_at": "2026-09-14T10:00:00Z"
  }
}
```

Times in evidence locators are segment-relative milliseconds. This supports
incremental segment submission, not an RTSP/WebRTC capture server. It has no
guaranteed real-time latency or automatic cross-segment tracking.

Text input uses the same source/options envelope plus `text`, maximum 100,000
characters. Multipart input uses `file` and `metadata` containing a JSON
source/options envelope. Source URLs are provenance only and are never fetched.
`options.translate_to` accepts `en` or `dv`; `options.transliterate` accepts
`latin` or `thaana`. Originals are preserved beside generated transformations.

### Acknowledgement

HTTP 202 returns `schema_version`, `analysis_id`, `source_id`, `source_type`, `attributes`,
`status`, `created`,
and `results_url`. The deduplication key contains authenticated owner, source
record, content hash, options, stream metadata and pipeline version. Identical
bytes associated with different BlackGlass records retain distinct mappings.
Replay the same submission to recover an acknowledgement lost in transit.

There is a 1,000-active-analysis admission limit per owner. HTTP 429 tells a
bulk sender to retry after the queue drains. Batch requests can commit earlier
items before encountering capacity or infrastructure failure: replaying the
whole batch is safe. Never advance a sender's cursor merely because an HTTP
request was transmitted; persist successful per-record acknowledgements.

## Worker and result semantics

`python -m app.evidence_worker` claims `evidence_analysis` jobs. PostgreSQL
leases, attempts, retries and dead-letter states are authoritative. Run time is
bounded; result and outbox commits are rejected after lease loss. A crash may
leave an unreferenced Qdrant point; search rechecks committed PostgreSQL evidence
and ownership before returning it. No original source is deleted during retry.

Result fields include `analysis_id`, `source`, `stream`, `source_sha256`,
`storage_mode`, `status`, `revision`, `evidence`, `findings`, `contradictions`,
`warnings`, `summary_provenance` and `vector_collection` when indexing succeeds.
States include `queued`, `running`, `retry`, `completed`, `partial`, `failed`.
Each piece includes exact original extraction, separately normalized text,
source locator, transformations, and producing model/version. The content URL
is scoped to the run owner rather than relying on possession of a media UUID.

Findings must cite existing evidence IDs and verbatim original extraction
quotes. Fabricated IDs/quotes are rejected. This verifies reference integrity,
**not semantic entailment, model accuracy, or factual truth**. All generated
findings remain `unreviewed`. An extraction can be wrong even when quoted
correctly; reviewers must inspect the retained original.

### Current model and format boundaries

* Text: exact Unicode source + NFC/whitespace normalization; multilingual E5
  vectors, lexical retrieval and optional cited Qwen summary.
* PDF: text-layer extraction with page references. Scanned pages explicitly
  report that layout OCR is still required; no invented text is returned.
* Images: bounded Qwen visual observations. Dhivehi specialist OCR applies to
  supplied text crops; it is not full-page layout recognition.
* Audio: explicitly selected `en` or `dv`, 20-second chunks with chunk-level
  timestamps. English uses the preinstalled faster-whisper artifact; Dhivehi
  uses the specialist service. Mixed-language speech requires separate accuracy
  evaluation and is not silently routed to an English-only transcription.
* Video: bounded sampled frames plus audio chunks. Unsampled moments are not
  inferred. Default 120 seconds / 6 frames; ceilings 600 seconds / 20 frames.
* Translation/transliteration: existing specialist adapters. Quality on mixed
  informal Dhivehi is unverified. No speaker identity or diarization is claimed.
* Summaries: every extracted evidence piece is considered in bounded batches of
  twelve. Up to 200 evidence pieces per run; extraction truncation is explicit.

Unsupported stages produce visible warnings or failure; installed model
artifacts are not reported as tested language accuracy.

## BlackGlass event receiver

Configure `EVIDENCE_WEBHOOK_URL` (HTTPS), `EVIDENCE_WEBHOOK_TOKEN` and
`EVIDENCE_WEBHOOK_OWNER` only after BlackGlass has implemented its receiver.
`EVIDENCE_DELIVERY_ENABLED` defaults to false. Events remain retrievable through
the replay API while delivery is disabled. Enable delivery on the analysis
worker for future events, then run `python -m app.evidence_worker --delivery`.
The delivery worker also discovers previously undelivered events for the configured
owner. Discovery is idempotent and does not reset exhausted delivery attempts.
With Compose, explicitly enable the `evidence-delivery` profile and service;
set `EVIDENCE_DELIVERY_ENABLED=true` and the receiver settings in `.env`.
No delivery worker is started by the ordinary `evidence` profile.

The receiver gets a complete `analysis.updated` snapshot with stable `event_id`,
`analysis_id`, `revision`, original `source_id`, optional stream metadata,
evidence, findings and warnings. Headers:
The payload also contains `report`, a BlackGlass-compatible schema-2 report with
31 ordered blocks. Unsupported analytical sections say that cited evidence is
insufficient; they are never completed from speculation. `document.evidenceRefs`
links every included extract to its locator and processing provenance.

Report analysis covers every extracted evidence piece in bounded batches. Each
generated observation declares its report section, confidence, basis and exact
post citation. Behaviour and routines require repeated observations. Identity
requires an explicit account field, identifier or self-identification rather
than appearance. Associations require a cited mention, reply, tag, shared event
or interaction. Risk and suspicious-activity sections describe cited indicators
and never label a person. Absence of a cited indicator is not treated as proof
that an indicator was checked or absent. Findings remain unreviewed.

```http
Authorization: Bearer <separate-BlackGlass-receiver-token>
Idempotency-Key: <event_id>
Content-Type: application/json
```

After durable storage, return a 2xx JSON acknowledgement:

```json
{"event_id":"<same-event-uuid>","status":"accepted"}
```

Return `duplicate` for a replay already stored. Redirects are not followed.
Wrong acknowledgements are failures. Delivery retries use the same event ID
and are bounded to eight attempts. Never send automatic user-facing alerts
from unreviewed findings; webhook synchronization and user notifications are
separate decisions. The BlackGlass application itself is not in this checkout.

## Local deployment and acceptance

Apply Alembic migrations on the intended deployment, rebuild its API with these
sources, and add `docker-compose.evidence.yml` to its existing compose command.
Start only the `evidence-worker` service with profile `evidence`; this does not
require starting the face worker. The evidence decoder image adds ffmpeg,
pypdf, faster-whisper to the selected API image. It is capped at 4 CPUs / 8 GiB.
It reuses existing language/speech model caches; its speech receipt lives at
`/cache/speech-model.json`, or set `EVIDENCE_ENGLISH_ASR_PATH` explicitly.
Worker presence is refreshed every 15 seconds, including during active jobs.
This does not extend the job's bounded lease. An interrupted job is recovered
through lease expiry and retry; shutdown does not discard the durable queue.
Scale analysis workers only after measuring inference-server capacity: each
replica has its own embedding model and up to 8 GiB memory allowance.

Run `python -m app.evidence_preflight` inside the configured service network to
inspect model availability. Add `--smoke` for one synthetic Qwen inference check.
That check establishes neither Dhivehi accuracy nor full multimodal readiness.

The frontend `/evidence` page supports manual text/file input, analysis lookup,
polling, hybrid search, source download, source excerpts, citation navigation,
and `dir=auto` for Thaana/mixed content. It does not grant access to another
service account's analyses or mark model findings verified.

Before bulk release, validate representative Dhivehi/English OCR, speech and
translation against reviewed references; verify retrieval/citation support,
Cyber-AI volume capacity/backup, decoder limits, crash recovery, queue
pressure, and the actual BlackGlass acknowledgement endpoint. A green unit test
run is not this acceptance sign-off. No unattended notifications are enabled.

### Verification checkpoint — 2026-09-15

* 19 isolated evidence integration tests passed against disposable PostgreSQL,
  including unified text-plus-file ingestion, local storage, replay, owner
  isolation, checksum/source handling, worker result publication, active
  heartbeat, delivery backfill and replay. One optional Qdrant test was skipped
  because that disposable endpoint was not started for this run.
* 88 existing frontend tests passed; frontend typecheck and targeted ESLint passed.
* Evidence Python lint and targeted type checking passed.
* Decoder worker image built locally. Compose overlay validated against cyber-ai's
  current deployment without applying it.
* Cyber-ai's local Qwen passed one synthetic structured-output inference test.
  Specialist availability returned installed states; no language-quality claim.

These changes are not yet deployed. The local checkout has no runtime `.env`.
Production rollout requires its intended environment, migration/rebuild and
explicitly starting the evidence services. Actual BlackGlass delivery requires
the receiver contract above and an end-to-end acknowledgement test. Full-page
OCR, mixed-language speech accuracy, direct live-feed capture and representative
load/quality evaluation remain outside this verified checkpoint.
