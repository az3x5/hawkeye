# BlackGlass evidence workflow

This workflow handles authorized text/documents, OCR, transcription, translation,
sampled visual observations, semantic indexing, and cited summaries. It does not
perform person identification, ownership inference, plate lookup, or cross-stream
person tracking. Existing face enrollment is a separate pipeline and is not
started by any endpoint or worker described here.

## Original evidence storage

For bulk AWS input, use **shared object manifests**. BlackGlass and EagleEye
reference the same retained original; EagleEye stores no second original file.
PostgreSQL holds extracted evidence and model provenance. Qdrant holds vectors.
An embedding is not an archive of the source. This workflow has no S3 delete,
copy, move, or lifecycle-policy mutation operation.

Set `EVIDENCE_SOURCE_BUCKET` and a slash-terminated `EVIDENCE_SOURCE_PREFIX`
(default prefix `blackglass/`) on both API and evidence worker. The bucket can
default to the existing `AWS_BUCKET`. Existing AWS credential aliases remain
supported. Grant read access to this prefix, including versioned object reads
when version IDs are provided. Bucket retention/lifecycle must preserve every
referenced original; this application does not override AWS lifecycle rules.
Prefer immutable keys plus bucket versioning. Every read verifies byte size and
SHA-256. Unversioned overwritten objects fail integrity validation rather than
silently changing the evidence.

## APIs implemented in this change

All paths begin `/api/v1/integrations/blackglass`.

| Method/path | Permission | Purpose |
| --- | --- | --- |
| POST `/evidence/text` | `language` | Preserve text; enqueue evidence analysis |
| POST `/evidence/media` | `media:write` | Multipart original upload for manual input |
| POST `/evidence/objects` | `media:write` | Register one retained AWS original |
| POST `/evidence/objects/batch` | `media:write` | Register up to 50 AWS manifests |
| GET `/evidence/{analysis_id}` | `media:read` | Source, processing state, excerpts, findings |
| GET `/evidence/{analysis_id}/report` | `media:read` | BlackGlass schema-2 report JSON |
| GET `/evidence/{analysis_id}/content` | `media:read` | Checksum-verified original download |
| GET `/evidence/events?cursor=0&limit=50` | `media:read` | Replay result events |
| GET `/evidence/status` | `media:read` | Live worker counts and caller's backlog |
| GET `/evidence-search?q=...&limit=10` | `media:read` | Lexical + semantic evidence retrieval |

The authenticated token **subject** owns each analysis. Read access requires
both the scope and the same subject. A UUID or asserted `source.system` does not
grant access to another account's content. Use a stable BlackGlass service
subject across token rotation and issue it the required scopes. No automatic
administrator cross-owner bypass is provided by these new endpoints.

### Shared original manifest

```json
{
  "schema_version": "1.0",
  "source": {
    "system": "blackglass-prod",
    "object_type": "video",
    "object_id": "BG-VIDEO-123",
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

HTTP 202 returns `schema_version`, `analysis_id`, `source`, `status`, `created`,
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
* Summaries: first 12 evidence pieces maximum; a warning exposes incomplete
  summary coverage. Up to 200 evidence pieces per run; truncation is explicit.

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
`analysis_id`, `revision`, original `source.object_id`, optional stream metadata,
evidence, findings and warnings. Headers:
The payload also contains `report`, a BlackGlass-compatible schema-2 report with
31 ordered blocks. Unsupported analytical sections say that cited evidence is
insufficient; they are never completed from speculation. `document.evidenceRefs`
links every included extract to its locator and processing provenance.

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
shared-bucket permissions/retention, decoder limits, crash recovery, queue
pressure, and the actual BlackGlass acknowledgement endpoint. A green unit test
run is not this acceptance sign-off. No unattended notifications are enabled.

### Verification checkpoint — 2026-09-14

* 16 isolated evidence integration tests passed against disposable PostgreSQL
  and Qdrant, including owner isolation, checksum/source handling, worker result
  publication, active heartbeat, delivery backfill and replay.
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
