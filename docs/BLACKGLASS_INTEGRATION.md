# BlackGlass integration

## Current supported path: manual delivery

An operator with `media:write` can open `/ingestion`, select a file exported
from BlackGlass, and provide its BlackGlass record ID. EagleEye sends the file
to `POST /api/v1/integrations/blackglass/media`. The server fixes the source to
`blackglass`, detects the
real file type, enforces upload limits, stores the bytes, records provenance,
and writes the authenticated operator to the audit trail.

The external record ID is required in the web workflow. Re-delivering the same
record and bytes returns the existing EagleEye asset instead of creating a
duplicate. The optional source URL is metadata only and is never fetched.

## Versioned service API

BlackGlass can push directly to EagleEye without using the operator UI:

- `GET /api/v1/integrations/blackglass/capabilities`
- `POST /api/v1/integrations/blackglass/media`
- `POST /api/v1/integrations/blackglass/text`

Media delivery is multipart and requires `external_object_id`,
`external_object_type`, a file, and an optional JSON or comma-separated
`requested_analyses` field. Text delivery uses JSON with the same versioned
source envelope. Both return an EagleEye subject UUID, idempotency state, and
an honest route for every requested analysis.

When media analyses are omitted, EagleEye chooses them after inspecting the
actual bytes: object detection and OCR for images, object detection/tracking/
transcription for video, transcription for audio, and OCR for PDF. A route
reported as `not_connected` is returned explicitly but does not falsely claim
that a production result worker has completed it.

The reference sender is `scripts/blackglass_to_eagleeye.py`. It reads the
credential from `EAGLEEYE_TOKEN`, never a command-line argument:

```bash
export EAGLEEYE_TOKEN='one-time-issued-service-token'
backend/.venv/bin/python scripts/blackglass_to_eagleeye.py \
  --eagleeye-url http://127.0.0.1:8000 \
  media --object-id bg-4471 --object-type post \
  --file ./evidence.jpg \
  --analyses face_identification,vehicle_detection,landmark_recognition,ocr
```

Browser intake is capped at 50 MiB. Larger authorized exports should use a
scoped service credential and the backend API directly:

```bash
curl -sS -X POST https://eagleeye.example/api/v1/integrations/blackglass/media \
  -H "Authorization: Bearer $EAGLEEYE_MEDIA_TOKEN" \
  -F source_system=blackglass-prod \
  -F external_object_id=bg-4471 \
  -F external_object_type=video \
  -F classification=internal \
  -F file=@/path/to/exported-evidence.mp4
```

## Pull synchronization still needs the BlackGlass contract

EagleEye does not yet call an undocumented BlackGlass endpoint. A production
pull or webhook connector requires the confirmed BlackGlass base URL,
authentication method, object and pagination schemas, media download rules,
rate limits, and deletion/update semantics. Credentials must remain in the
server secret store; they must never be entered into or returned to the web
browser.

Once that contract is available, the automated path should use durable jobs,
an external-object mapping, a sync cursor, bounded retry, replay protection,
and contract tests against a local BlackGlass stub.
