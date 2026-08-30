# Durable processing jobs

EagleEye uses PostgreSQL as the source of truth for asynchronous work. Face
enrolment and language indexing share this protocol. Redis is not required for
durability; it remains the rate-limit store and a legacy-queue cutover source.

## Ownership and data boundaries

The producer writes its domain record and `processing.jobs` row in the same
database transaction. A job payload contains routing identifiers only. It must
not contain media bytes, embeddings, raw document text, access tokens,
credentials, or secrets. Binary values are rejected by the domain contract.
Workers retrieve source content through the appropriate storage connector.

PostgreSQL owns job metadata and history, object storage owns source bytes and
derived artifacts, and Qdrant owns vectors. An outbox event is inserted in the
same transaction as every job transition. No external outbox relay is enabled
in M1; later integrations can deliver those rows without changing job truth.

## Tables

| Table | Purpose |
| --- | --- |
| `processing.jobs` | Current state, pipeline, payload, priority, schedule, lease, retry budget, result, and typed failure |
| `processing.job_attempts` | Immutable claim/outcome history and fencing token for each attempt |
| `processing.worker_heartbeats` | Last-seen time and queue for each worker identity |
| `processing.outbox_events` | Stable transition name and sanitized identifiers for future delivery |

The stable event names are `processing.job.queued`, `.claimed`, `.completed`,
`.failed`, `.retried`, `.cancelled`, and `.lease_expired`.

## State machine and claiming

```text
queued ──claim──> running ──complete──> completed
  ▲                  │
  │                  ├─retryable failure──> queued (delayed by backoff)
  │                  ├─non-retryable──────> failed
  │                  ├─budget exhausted───> dead_letter
  │                  └─lease expires──────> queued during a later claim cycle
  └────admin retry──── failed/dead_letter/cancelled

queued/running ──admin cancel──> cancelled
```

Claims run in a short transaction using `FOR UPDATE SKIP LOCKED`, ordered by
priority and creation time. A successful claim increments the attempt number,
sets `lease_owner` and `leased_until`, assigns a new fencing token, records an
attempt, emits a claimed event, and commits before model inference begins.

Only the current owner with the current fencing token and an unexpired lease
may complete or fail a job. This prevents a slow worker from committing after
another worker has reclaimed the job. Heartbeats show worker liveness but do
not replace job leases.

## Retry and failure policy

Failures use typed codes such as `source_unavailable`, `invalid_source`,
`model_unavailable`, `inference_failed`, `vector_store_unavailable`, and
`internal_error`. The worker classifies whether a failure is retryable.

Retry delay is bounded exponential backoff:

```text
delay = min(retry_max_seconds,
            retry_base_seconds * 2 ** (attempt_number - 1))
```

The job becomes claimable at `available_at`. When `max_attempts` is exhausted,
retryable work enters `dead_letter`. A non-retryable terminal failure enters
`failed`. Configuration is controlled by `FACEID_JOB_LEASE_SECONDS`,
`FACEID_JOB_POLL_SECONDS`, `FACEID_JOB_MAX_ATTEMPTS`,
`FACEID_JOB_RETRY_BASE_SECONDS`, and `FACEID_JOB_RETRY_MAX_SECONDS`.

## Administration API

All processing administration endpoints require the `admin` scope:

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/processing/jobs` | Filter and page through jobs |
| `GET` | `/api/v1/processing/jobs/summary` | Counts, queue age, leases, throughput, and live workers |
| `GET` | `/api/v1/processing/jobs/{job_uuid}` | Job plus attempt history |
| `POST` | `/api/v1/processing/jobs/{job_uuid}/retry` | Requeue a failed or dead-letter job |
| `POST` | `/api/v1/processing/jobs/{job_uuid}/cancel` | Cancel queued or running work |

Retry and cancel actions append an audit event. The API never returns secret or
binary content because such content is not valid in a job payload.

## Metrics and alerts

`GET /api/v1/system/metrics` and the dashboard expose:

- counts by state, including failed and dead-letter work;
- queue depth and age of the oldest claimable job;
- active and expired leases;
- completed jobs and attempts during the last minute;
- live worker heartbeat count.

Recommended initial alerts, tuned after observing production traffic:

- oldest queue age exceeds twice the normal end-to-end service objective;
- claimable depth rises for three collection intervals;
- expired leases remain non-zero for more than one lease duration;
- `dead_letter` or failed count increases;
- no live worker exists while claimable work exists;
- attempts/minute rises while completions/minute stays flat.

These are operational signals, not fixed product thresholds. Persist metrics in
an external time-series system before creating trend or rate promises.

## Operator recovery runbook

1. Open the processing summary and confirm queue depth, oldest age, expired
   leases, and live workers.
2. Inspect the job and all attempts. Use the typed error code and identifiers to
   check source storage, model readiness, PostgreSQL, and Qdrant.
3. Restore the unavailable dependency. Do not edit job rows manually.
4. Retry only `failed` or `dead_letter` work through the admin API. The action
   resets the scheduling state but retains attempt history and writes audit and
   outbox events.
5. Cancel queued/running work only when its side effect is no longer wanted.
   The tested admin retry transition may requeue cancelled work when that is a
   deliberate operator decision.
6. If a worker died, start a healthy replacement. Its claim cycle will expire
   stale leases and reclaim eligible work; no database repair is required.
7. If duplicate downstream effects are suspected, inspect the job idempotency
   key, attempt fencing tokens, domain record, and vector identifier before any
   manual reconciliation.

## Legacy cutover and rollback

Workers recover their legacy Redis ready/in-flight lists at startup and enqueue
equivalent PostgreSQL jobs with stable idempotency keys. New producers write
PostgreSQL directly. Confirm Redis legacy depth is zero before removing the
compatibility code.

For rollback, first stop producers and workers, then inventory all non-terminal
PostgreSQL jobs and preserve processing history. Re-enable the compatible
worker/producer version and reconcile by idempotency key. Never downgrade the
M1 migration while authoritative work or required attempt/outbox history exists:
the downgrade removes the entire `processing` schema.

## Current limitations

- The outbox is durable but has no delivery relay; BlackGlass delivery belongs
  to a later integration phase.
- Job metrics are current snapshots and one-minute database counts, not a
  retained time series.
- PostgreSQL polling is deliberate for the local-first baseline. A later SQS
  or notification adapter may accelerate dispatch while PostgreSQL remains the
  domain record or is migrated through an explicit queue contract.
