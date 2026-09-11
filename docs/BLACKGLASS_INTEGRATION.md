# BlackGlass integration

## Current supported path: manual delivery

An operator with `media:write` can open `/ingestion`, select a file exported
from BlackGlass, and provide its BlackGlass record ID. EagleEye sends the file
to `POST /api/v1/media` with `source_type=blackglass`. The server detects the
real file type, enforces upload limits, stores the bytes, records provenance,
and writes the authenticated operator to the audit trail.

The external record ID is required in the web workflow. Re-delivering the same
record and bytes returns the existing EagleEye asset instead of creating a
duplicate. The optional source URL is metadata only and is never fetched.

Browser intake is capped at 50 MiB. Larger authorized exports should use a
scoped service credential and the backend API directly:

```bash
curl -sS -X POST https://eagleeye.example/api/v1/media \
  -H "Authorization: Bearer $EAGLEEYE_MEDIA_TOKEN" \
  -F source_type=blackglass \
  -F source_system=blackglass-prod \
  -F external_source_id=bg-4471 \
  -F classification=internal \
  -F file=@/path/to/exported-evidence.mp4
```

## Automated synchronization is not configured

EagleEye does not yet call an undocumented BlackGlass endpoint. A production
pull or webhook connector requires the confirmed BlackGlass base URL,
authentication method, object and pagination schemas, media download rules,
rate limits, and deletion/update semantics. Credentials must remain in the
server secret store; they must never be entered into or returned to the web
browser.

Once that contract is available, the automated path should use durable jobs,
an external-object mapping, a sync cursor, bounded retry, replay protection,
and contract tests against a local BlackGlass stub.
