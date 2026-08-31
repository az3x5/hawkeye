# Media pipeline

**Status: IMPLEMENTED (M2).** Ingestion, provenance, lineage, retention holds,
erasure and the dual-backend storage seam exist and are tested. Sections
marked **(planned)** name where later phases attach.

Media is the substrate every later modality sits on: OCR (M6), image
intelligence (M7), vehicles (M8), video (M9) and audio (M10) all consume media
assets and write derivatives back. This document is the contract they build
against.

## 1. Why media is a first-class asset

Before M2 an image was a SHA-256 mentioned by a `face_samples` row or an
`identifications` row. That is sufficient for one modality with one owner and
insufficient for anything else: it cannot say what a file *is*, where it came
from, who may see it, or what was computed from it.

`media.assets` answers those questions, and three properties make the answers
trustworthy.

**One asset, many sources.** The same photograph arrives from an upload, a
BlackGlass record and a news article. Deduplicating on the content hash must
not collapse the fact that it arrived three times from three places, because
the arrival is what a later claim cites. Provenance is therefore a separate
row per arrival in `media.asset_sources`, added on every ingestion and never
removed.

**Derivation is recorded, not implied.** A thumbnail, a face crop and an
extracted audio track are themselves assets, linked through
`media.asset_derivatives` to the asset and the transform that produced them.
Reprocessing adds lineage rather than overwriting it, so a result from an old
model version stays attributable to that version.

**Deletion has a veto.** `media.retention_holds` blocks erasure of material
somebody has said must be kept. "Erase everything for this person" cannot
quietly destroy evidence under hold; it fails loudly instead.

## 2. Ingestion

```text
bytes
 ↓  bounded read          refuse past the ceiling while reading
 ↓  magic-byte sniff      the file decides its type, not the caller
 ↓  declared-type check   a contradicting Content-Type is refused
 ↓  pixel-ceiling check   header dimensions, before any decoder allocates
 ↓  SHA-256
 ↓  existing hash?
      ├── yes → add a source record, return the existing asset
      └── no  → write the object, then commit the row
```

### Order of writes

Bytes go to object storage **before** the row that names them. The reverse
order produces dangling metadata on a crash, which is the harder failure to
detect: a missing object looks like corruption, while an orphaned object is
found by a reconciliation sweep and costs only disk until then.

### Idempotency

Ingestion is idempotent on content. Identical bytes converge on one
`media_uuid`. A concurrent race between two identical ingestions is resolved
by converging on whichever row won, because the object is content-addressed
and therefore shared.

Provenance is idempotent on `(media_uuid, source_type, source_system,
external_source_id)`. A collector that re-delivers the same arrival does not
accumulate duplicate rows. A `NULL` external id is deliberately *not*
deduplicated: two anonymous uploads of one image really are two arrivals.

## 3. Type identification and file safety

`app/domain/content_types.py` identifies media from its bytes.

A browser's `Content-Type` and a filename's extension are both attacker
controlled. Accepting an upload because its declared type was in an allowlist
means the allowlist describes the *claim* rather than the file. So:

- the format is decided by magic bytes;
- a declared type that **disagrees** with the content is a rejection, not a
  correction — the disagreement is itself the signal;
- a declared type that is absent or `application/octet-stream` is fine,
  because the bytes are authoritative either way.

Recognised formats: JPEG, PNG, WebP, GIF, TIFF, BMP; PDF; MP4, WebM,
Matroska, QuickTime, AVI; WAV, MP3, FLAC, Ogg, M4A. Containers that share a
header are separated properly — RIFF splits into WebP, WAV and AVI; EBML into
WebM and Matroska; ISO base media into MP4, QuickTime and M4A audio.

### Decompression bombs

Image dimensions are parsed from the header (PNG `IHDR`, JPEG SOF markers,
GIF logical screen, WebP VP8/VP8L/VP8X) and checked against
`MAX_IMAGE_PIXELS` (80 million). A 40-byte PNG declaring 60000×60000 is
refused before any decoder is asked to allocate roughly 10 GB for it.

### Upload bounds

`read_bounded_upload` reads in 1 MiB chunks and refuses as soon as the total
passes the ceiling. Reading the whole body and then measuring it lets a caller
decide how much memory the API allocates — the exact denial of service the
limit appears to prevent but does not.

Ceilings: `FACEID_MEDIA_MAX_UPLOAD_BYTES` (default 256 MiB) for media
ingestion; `MAX_IMAGE_BYTES` (10 MiB) for face enrolment, unchanged.

## 4. Storage seam

```text
        BlobStore  (app/domain/storage.py)
             |
      +------+------+
      |             |
FilesystemBlobStore  S3BlobStore
  local directory    MinIO locally, S3 in AWS
```

Addresses are `ObjectLocation(bucket, key)` — the two things every object
store in use has. The type validates bucket names and refuses path traversal
on construction, so an invalid address never reaches a backend. The filesystem
backend re-checks that the resolved path stays inside its root: two checks for
one property is deliberate, because that class turns caller-supplied strings
into filesystem paths, which is where a single missed validation becomes an
arbitrary write.

Both backends pass the **same** contract suite (`tests/test_blob_store.py`,
parametrised over the backends). That is what makes the AWS migration a
configuration change rather than a rewrite.

### Buckets

| Storage domain | Bucket | Holds |
| --- | --- | --- |
| `media` | `eagleeye-media` | Source bytes of any modality |
| `derived` | `eagleeye-derived` | Thumbnails, crops, extracted tracks **(planned users)** |
| `biometric-reference` | `eagleeye-face-reference` | Deliberately enrolled faces **(planned users)** |
| `biometric-observed` | `eagleeye-face-observations` | Faces found in collected media **(planned users)** |

Separate buckets exist so a deployment can grant a worker read access to
general media without also granting it reference biometrics — a policy a
single bucket cannot express. The biometric buckets are created but not yet
written to; the face pipeline still uses the legacy object store (§7).

### Keys

`{first two hex chars of digest}/{digest}.{extension}`

The shard prefix keeps filesystem directories small; object stores do not need
it, but one layout across both backends makes a migration a copy rather than a
re-addressing. The extension is cosmetic — the content type is stored as
object metadata and the digest is the real address — but it lets an operator
browsing a bucket tell a video from a document.

## 5. Classification and authorisation

| Classification | Meaning |
| --- | --- |
| `public` | No access restriction beyond authentication |
| `internal` | Default for collected material |
| `restricted` | Sensitive; reads are audited |
| `biometric` | Biometric material; reads are audited, separate legal basis |

`biometric` is separate from `restricted` rather than more severe: it is a
different kind of sensitivity with its own legal basis, not a higher grade of
the same one.

Scopes:

| Scope | Permits |
| --- | --- |
| `media:write` | Submit media |
| `media:read` | Read metadata and fetch bytes |
| `admin` | Erase media, place and release retention holds |

`media:read` and `media:write` are separate and neither implies the other.
Erasure and holds require `admin`, not `media:write`.

Reads of `restricted` and `biometric` assets emit a `media_viewed` audit
event. Ordinary media does not: auditing every thumbnail fetch buries the
reads that matter under the ones that do not. Fetching metadata is not a view
of the material and is not audited.

## 6. Erasure and retention

Erasing an asset removes the **bytes** and keeps the **row**, marking it
`erased` with `erased_at`. The content hash outlives the material, so a
decision that cited this media stays explicable after the personal or
biometric content behind it is gone. A database check constraint enforces that
`status = 'erased'` and `erased_at IS NOT NULL` agree, so the readable/erased
split is an invariant rather than a convention.

Erasure is refused while any hold is active, and both erasure and hold
lifecycle changes are audited with actor, reason and asset identifiers —
never with bytes.

## 7. Relationship to the existing face pipeline

The face pipeline keeps its digest-addressed `FilesystemObjectStore` and its
existing data. M2 deliberately does **not** migrate it:

- moving live biometric objects is a data migration with a real failure mode,
  and it is not needed to deliver first-class media;
- the enrolment and face-sample-image endpoints keep their exact contracts.

What the face path *did* gain is the hardened intake: `read_image_upload` now
uses the bounded reader and magic-byte identification, so a PDF named
`photo.jpg` and declared `image/jpeg` is refused. Enrolment still answers 422
for both oversized and unrecognised images, as it always has.

Migrating face objects onto the media asset model is future work and belongs
with M3 (trust boundaries and storage isolation), where the reference/observed
biometric split is introduced.

## 8. Configuration

```bash
FACEID_OBJECT_STORE_BACKEND=filesystem|s3   # default filesystem
FACEID_S3_ENDPOINT_URL=http://minio:9000    # unset for real AWS S3
FACEID_S3_REGION=us-east-1
FACEID_S3_ACCESS_KEY=...
FACEID_S3_SECRET_KEY=...
FACEID_S3_USE_PATH_STYLE=true               # true for MinIO, false for S3
FACEID_MEDIA_MAX_UPLOAD_BYTES=268435456
FACEID_MEDIA_PAGE_SIZE_LIMIT=200
```

Settings validation refuses to start with `object_store_backend=s3` and no
credentials. Failing at startup is far kinder than failing on the first
upload, when the media is already in flight and the caller cannot tell a
configuration mistake from an outage.

Compose runs the `s3` backend against MinIO by default. MinIO publishes only
to loopback, and its browser console is off: it is an administrative surface
with no place on a machine that only needs the object API.

## 9. API

| Method | Path | Scope |
| --- | --- | --- |
| `POST` | `/api/v1/media` | `media:write` |
| `GET` | `/api/v1/media` | `media:read` |
| `GET` | `/api/v1/media/{media_uuid}` | `media:read` |
| `GET` | `/api/v1/media/{media_uuid}/sources` | `media:read` |
| `GET` | `/api/v1/media/{media_uuid}/content` | `media:read` |
| `DELETE` | `/api/v1/media/{media_uuid}` | `admin` |
| `POST` | `/api/v1/media/{media_uuid}/holds` | `admin` |
| `GET` | `/api/v1/media/{media_uuid}/holds` | `media:read` |
| `DELETE` | `/api/v1/media/holds/{hold_uuid}` | `admin` |

Content responses carry `Cache-Control: private, no-store`,
`Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`: the
bytes are caller-supplied and nothing should sniff or execute them. Bytes are
never served by content hash — a caller must name an asset that exists, so
holding a digest is not on its own enough to pull material out of the store.

Single-range requests are supported, capped at 8 MiB per response so a range
request cannot pull an arbitrarily large object into memory in one call.
Listing clamps `limit` to the server ceiling rather than rejecting it.

## 10. Schema

```text
media.assets              one row per distinct byte sequence (sha256 unique)
media.asset_sources       one row per arrival; never deleted
media.asset_derivatives   lineage; source and derived are both assets
media.retention_holds     standing instructions not to erase
```

Migration `c3f7a91d8b40`. Its downgrade drops the schema and every media row;
the object bytes survive in the bucket, but the rows saying what they are and
where they came from do not. Export before running it in anger.

## 11. AWS mapping

| Local | AWS |
| --- | --- |
| MinIO | S3 |
| `S3BlobStore` with endpoint URL | `S3BlobStore` without endpoint URL |
| path-style addressing | virtual-host addressing |
| MinIO root credentials | IAM task role |
| bucket per storage domain | same buckets, separate KMS keys and policies |

No domain or service code changes. The seam is the deliverable.

## 12. Known limitations

- Face and identification images are not yet media assets (§7).
- No derivative *producer* exists yet; the lineage table is written by later
  phases (M6–M10).
- No signed-URL access. Bytes are proxied through the API, which keeps
  authorisation in one place and costs API bandwidth. Revisit when large video
  arrives in M9.
- Uploads are buffered in memory up to the ceiling rather than streamed to
  storage. Adequate at a 256 MiB ceiling; streaming multipart is M9 work.
- Video and audio duration are not probed; `duration_ms` stays `NULL` until
  M9/M10 add a container probe.
- No server-side encryption at rest beyond what the backend provides. SSE-KMS
  is M18.
- Remote URL ingestion does not exist, deliberately: it needs the SSRF-safe
  downloader specified for M4, and adding a generic HTTP fetch first would be
  the vulnerability that document exists to prevent.
