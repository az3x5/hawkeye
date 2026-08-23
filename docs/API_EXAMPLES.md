# Hawkeye — API examples

Every command below was run against a local stack and works as written. The
API is on `http://127.0.0.1:8000`; only `/health`, `/readyz` and `/sessions`
are reachable without a credential.

## 1. Sign in

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@admin.com","password":"YOUR-PASSWORD"}'
```

Keep the token in a shell variable for everything that follows:

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d '{"email":"admin@admin.com","password":"YOUR-PASSWORD"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
```

Confirm who you are:

```bash
curl -sS http://127.0.0.1:8000/api/v1/me -H "Authorization: Bearer $TOKEN"
```

Sign out, revoking the session server-side:

```bash
curl -sS -X DELETE http://127.0.0.1:8000/api/v1/sessions/current -H "Authorization: Bearer $TOKEN"
```

## 2. Health

No credential needed. `/readyz` reports every dependency and answers 503 if any
is unusable.

```bash
curl -sS http://127.0.0.1:8000/api/v1/health
curl -sS http://127.0.0.1:8000/api/v1/readyz
```

## 3. Enrol a face — scope `enrol`

`source` plus at least one of `external_id` / `local_id` is required.
Idempotent: the same image for the same person returns the original identifiers
and schedules no further work.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/enrolments \
  -H "Authorization: Bearer $TOKEN" \
  -F source=crm -F external_id=alice \
  -F "image=@alice.jpg;type=image/jpeg"
```

Answers 202 with `processing_state: pending`. The worker embeds it within a
second or two; poll until it leaves `pending`:

```bash
curl -sS http://127.0.0.1:8000/api/v1/face-samples/$SAMPLE -H "Authorization: Bearer $TOKEN"
```

`failed` carries a `failure_reason` — "no face was detected in the image",
"2 faces were detected; enrolment requires exactly one", and so on.

## 4. Identify — scope `identify`

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/identifications \
  -H "Authorization: Bearer $TOKEN" \
  -F "image=@query.jpg;type=image/jpeg"
```

Returns `outcome` (`accept` / `review` / `reject`), the candidates with raw
cosine `score`s, the `margin` to the runner-up, and the thresholds that
produced the decision. Scores are **not** probabilities.

## 5. Review — scope `review`

```bash
curl -sS "http://127.0.0.1:8000/api/v1/identifications?limit=50" -H "Authorization: Bearer $TOKEN"
curl -sS http://127.0.0.1:8000/api/v1/identifications/$ID -H "Authorization: Bearer $TOKEN"
```

Only proposals whose outcome was `review` can be reviewed, and only once. The
reviewer is taken from the credential, not the body:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/identifications/$ID/review \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"outcome":"confirmed","note":"same person, different lighting"}'
```

Images, for comparing a query against a candidate's enrolled sample:

```bash
curl -sS http://127.0.0.1:8000/api/v1/identifications/$ID/image -H "Authorization: Bearer $TOKEN" -o query.jpg
curl -sS http://127.0.0.1:8000/api/v1/face-samples/$SAMPLE/image -H "Authorization: Bearer $TOKEN" -o sample.jpg
```

## 6. Erase a person — scope `admin`

Removes metadata, embeddings across every model provenance, and stored images.
Irreversible, and audited.

```bash
curl -sS -X DELETE "http://127.0.0.1:8000/api/v1/persons/$PERSON?reason=subject+request" \
  -H "Authorization: Bearer $TOKEN"
```

## 7. Administration — scope `admin`

Accounts:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/accounts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"someone@example.com","password":"a-long-enough-password","scopes":["review"]}'

curl -sS http://127.0.0.1:8000/api/v1/accounts -H "Authorization: Bearer $TOKEN"

curl -sS -X POST http://127.0.0.1:8000/api/v1/accounts/$USER/disable -H "Authorization: Bearer $TOKEN"
curl -sS -X POST http://127.0.0.1:8000/api/v1/accounts/$USER/enable  -H "Authorization: Bearer $TOKEN"
```

Change your own password (needs no admin scope, but does need the current one;
revokes every session afterwards):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/me/password \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"current_password":"OLD","new_password":"a-new-long-password"}'
```

Service credentials. The secret is shown once and is not recoverable:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/tokens \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"subject":"ingest","kind":"service","scopes":["enrol","identify"],"expires_in_days":30}'

curl -sS http://127.0.0.1:8000/api/v1/tokens -H "Authorization: Bearer $TOKEN"
curl -sS -X DELETE http://127.0.0.1:8000/api/v1/tokens/$TOKEN_UUID -H "Authorization: Bearer $TOKEN"
```

## Errors

Every failure uses one envelope, so `error.code` is the thing to branch on:

```json
{"error": {"code": "not_authorised", "message": "...", "field": null}, "details": []}
```

| Status | Typical codes |
| --- | --- |
| 401 | `not_authenticated`, `sign_in_failed` |
| 403 | `not_authorised`, `wrong_password` |
| 404 | `face_sample_not_found`, `identification_not_found`, `person_not_found`, `token_not_found`, `account_not_found` |
| 409 | `conflicting_identifiers`, `review_not_permitted`, `account_conflict` |
| 422 | `validation_error`, `invalid_enrolment`, `identification_failed`, `invalid_password` |
| 429 | `rate_limited` (carries `Retry-After`) |
| 503 | `service_unavailable` |

Interactive docs, in the `local` environment only: <http://127.0.0.1:8000/docs>
