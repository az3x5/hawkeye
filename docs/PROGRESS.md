# EagleEye — progress against the master prompt

Generated: 2026-08-31 · Commit: `7c9675d` + uncommitted M2 work · Migration head: `c3f7a91d8b40`

Measured against the 160-section Codex Master Prompt. Section numbers below (`§n`)
refer to that document, so any claim here can be traced back to the requirement
it satisfies.

**Nothing is marked complete on the strength of a file existing.** Every
`IMPLEMENTED` row below has automated tests behind it, and the verification
figures were produced by running the commands, not by inference.

---

## 1. Verified baseline

All figures from a run on 2026-08-31 against live PostgreSQL, Redis, Qdrant and
MinIO, with real SCRFD and AdaFace weights loaded.

| Check | Command | Result |
| --- | --- | --- |
| Backend suite | `pytest -q` | **734 passed, 0 skipped** (78 s) |
| Lint | `ruff check .` | clean |
| Format | `ruff format --check .` | 145 files clean |
| Types | `mypy` (strict) | clean, 133 source files |
| Frontend types | `tsc --noEmit` | clean |
| Frontend lint | `eslint .` | clean |
| Frontend tests | `vitest run` | 74 passed |
| Frontend build | `next build` | succeeds |
| Migrations | `alembic upgrade head` on empty DB | 11 revisions, clean |
| Migration reversal | up → down 1 → up → down to base | clean |
| Compose | base, dev and deploy overlays | all validate |
| Container | `docker compose build api` + `up` | builds (1.76 GB), `/readyz` all 5 probes healthy |

There are **no skipped tests** in the local configuration. Model-dependent tests
skip only when weights are absent (§136); weights are present here, so they run.

---

## 2. Phase status

The roadmap in `MASTER_IMPLEMENTATION_PLAN.md` re-sequences the prompt's §141
phase list against actual repository state, as §147 requires.

| Phase | Name | Status |
| --- | --- | --- |
| M0 | Trustworthy baseline and architecture record | ✅ COMPLETE |
| M1 | Durable processing core | ✅ COMPLETE |
| M2 | First-class media and local object storage | ✅ COMPLETE |
| M3 | Trust boundaries, authorization, storage isolation | ⬜ NOT STARTED |
| M4 | BlackGlass API integration foundation | ⬜ NOT STARTED |
| M5 | Content, chunks, multilingual retrieval | ⬜ NOT STARTED |
| M6 | OCR and document analysis | ⬜ NOT STARTED |
| M7 | General image intelligence | ⬜ NOT STARTED |
| M8 | Vehicle intelligence | ⬜ NOT STARTED |
| M9 | Video intelligence | ⬜ NOT STARTED |
| M10 | Audio intelligence | ⬜ NOT STARTED |
| M11 | Universal entities and resolution | ⬜ NOT STARTED |
| M12 | Evidence, claims, observations, model registry | ⬜ NOT STARTED |
| M13 | LLM understanding and evidence-grounded RAG | ⬜ NOT STARTED |
| M14 | Events, narratives, universal search | ⬜ NOT STARTED |
| M15 | Analyst workflows and explainability | ⬜ NOT STARTED |
| M16 | Security verification and hardening | ⬜ NOT STARTED |
| M17 | Performance, observability, backup, DR | ⬜ NOT STARTED |
| M18 | AWS deployment readiness | ⬜ NOT STARTED |

**3 of 19 phases complete.** The three that are done are the foundational ones:
everything from M4 onward writes through the durable job protocol (M1) and the
media asset model (M2).

---

## 3. What is implemented

### Face intelligence — §25–§33

| Capability | State | Evidence |
| --- | --- | --- |
| SCRFD detection | IMPLEMENTED | `app/adapters/scrfd.py`, checksum-verified weights, real-model tests |
| AdaFace IR-101 recognition, 512-d normalized | IMPLEMENTED | `app/adapters/adaface.py`, strict state loading |
| Alignment and preprocessing versioning | IMPLEMENTED | `app/adapters/preprocessing.py` |
| Idempotent enrolment (§28) | IMPLEMENTED | `app/services/enrolment.py`; converges on races |
| Person → many samples → many embeddings (§29) | IMPLEMENTED | `face_samples` / `face_embeddings` split |
| Face search with person-level candidates (§30) | IMPLEMENTED | `app/services/identification.py` |
| Embedding provenance triple (§27) | IMPLEMENTED | model/version/preprocessing recorded per vector |
| Incomparable vectors physically separated | IMPLEMENTED | `app/connectors/qdrant/naming.py` derives collection from provenance |
| No fabricated thresholds (§76) | IMPLEMENTED | `decision_*` settings have **no defaults**; the app refuses to start without them |
| Human review of uncertain decisions | IMPLEMENTED | review queue, API and UI |

The threshold handling deserves a note, because it is the part most systems get
wrong: there is deliberately no default accept/review threshold in code. A
matching threshold shipped as a constant is a hard-coded production threshold
that nobody can explain after a contested decision, so every deployment must
state its own.

### Durable processing — §79, §81, §82, §83, §78

| Capability | State | Evidence |
| --- | --- | --- |
| PostgreSQL-authoritative jobs | IMPLEMENTED | `processing.jobs`; Redis is not job truth |
| Full status set (§81) | IMPLEMENTED | queued/running/completed/failed/retry/dead_letter/cancelled |
| Priority queues (§82) | IMPLEMENTED | 5 levels; bulk cannot block operational work |
| Leases and fencing tokens | IMPLEMENTED | a killed worker cannot strand a job |
| Immutable attempt history | IMPLEMENTED | `processing.job_attempts` |
| Bounded exponential retry → dead letter | IMPLEMENTED | operator retry/cancel, audited |
| Typed failure codes (§83) | IMPLEMENTED | `app/domain/processing.py` |
| Transactional outbox | IMPLEMENTED | `processing.outbox_events` — the durable event boundary M4 needs |
| Worker heartbeats | IMPLEMENTED | `processing.worker_heartbeats` |
| Partial-failure state (§78) | IMPLEMENTED | embedding metadata PENDING → ACTIVE/FAILED, plus reconciliation |

### Media core and object storage — §12, §20, §21, §22, §104

| Capability | State | Evidence |
| --- | --- | --- |
| `media.assets` first-class (§20) | IMPLEMENTED | one row per distinct byte sequence, `sha256` unique |
| `media.asset_sources` provenance (§21) | IMPLEMENTED | one row per *arrival*; never deleted |
| Dedup that preserves provenance (§22) | IMPLEMENTED | same bytes converge; every arrival retained |
| Derivative lineage | IMPLEMENTED (no producer yet) | `media.asset_derivatives` |
| Retention holds vetoing erasure | IMPLEMENTED | `media.retention_holds`; erasure returns 409 while held |
| `ObjectStorage` interface (§12) | IMPLEMENTED | `BlobStore` + filesystem and S3 implementations |
| MinIO locally, S3 semantics | IMPLEMENTED | one contract suite passes against **both** backends |
| Domain code free of MinIO/AWS SDK types (§131) | IMPLEMENTED | boto3 confined to `app/connectors/s3/` |
| Magic-byte validation (§104) | IMPLEMENTED | `app/domain/content_types.py`; declared MIME never decides |
| Decompression-bomb resistance (§104) | IMPLEMENTED | header pixel ceiling before any decoder allocates |
| Bounded upload reads (§105) | IMPLEMENTED | refused while reading, not after buffering |

The dual-backend contract suite is the load-bearing evidence for §14 and §126:
the AWS migration is a configuration change because both implementations are
proven against the same tests, not because a document says so.

### Storage responsibilities — §8, §9, §10

| Rule | State | Evidence |
| --- | --- | --- |
| PostgreSQL is system of record (§9) | IMPLEMENTED | identity, provenance, jobs, media, audit |
| No large binaries in PostgreSQL (§9) | IMPLEMENTED | bytes live in the blob store |
| Qdrant for vectors only (§10) | IMPLEMENTED | never authoritative |
| Minimal Qdrant payloads (§10) | IMPLEMENTED | identifiers and `active` flag only |
| Redis not the durable record (§13) | IMPLEMENTED | rate limiting and legacy cutover only |

### Security — §98, §100, §101, §103, §104, §105, §122, §157

| Control | State | Evidence |
| --- | --- | --- |
| Argon2id passwords, hashed bearer tokens | IMPLEMENTED | secrets never stored recoverably |
| Token expiry and revocation | IMPLEMENTED | `api_tokens` |
| Scope-based authorization, backend-authoritative (§93) | IMPLEMENTED | 7 scopes; frontend hiding is not relied on |
| `media:read` / `media:write` neither implies the other | IMPLEMENTED | authorization matrix tested |
| Append-only audit (§95) | IMPLEMENTED | no update, no delete |
| Biometric payloads stripped from logs (§154) | IMPLEMENTED | `SENSITIVE_KEYS` redaction, tested |
| Sensitive-read audit | IMPLEMENTED | restricted/biometric media reads logged; ordinary reads are not |
| Data services unpublished (§98, §157) | IMPLEMENTED | deploy overlay publishes only `api` and `frontend` |
| MinIO console disabled | IMPLEMENTED | `MINIO_BROWSER=off` |
| `.env` git-ignored, `.env.example` provided (§100) | IMPLEMENTED | no committed credentials |
| Real identity data protected from commit | IMPLEMENTED | `CSV/` git-ignored (21 MB of national ID records) |
| Parameterized queries only (§101) | IMPLEMENTED | SQLAlchemy Core throughout |
| Non-root containers (§110) | IMPLEMENTED | `USER faceid` |
| JSON structured logging (§122) | IMPLEMENTED | `app/core/logging.py` |
| Bytes never served by content hash | IMPLEMENTED | a digest alone cannot pull material out |

### Multilingual — §46

| Capability | State | Evidence |
| --- | --- | --- |
| Thaana / Latin / mixed normalization | IMPLEMENTED | `app/services/language.py` |
| Rule-based transliteration both directions | IMPLEMENTED | tested |
| Multilingual E5 embeddings + semantic search | IMPLEMENTED | pinned model revision recorded per document |

Labelled PARTIAL overall in §140 terms: the slice works, but there is no
Dhivehi retrieval benchmark, so no claim is made that E5 is the right model
(§46 explicitly forbids assuming it).

### Engineering discipline — §130–§136

| Rule | State |
| --- | --- |
| Clean architecture, dependency inversion, typed interfaces (§130) | IMPLEMENTED |
| Domain free of framework/SDK imports (§131) | IMPLEMENTED |
| `/api/v1/...` versioning (§132) | IMPLEMENTED |
| Forward-only migrations, upgrade path tested (§133) | IMPLEMENTED |
| Unit, integration, API, authorization, migration, worker, adapter tests (§134) | IMPLEMENTED |
| Model tests use real weights or skip explicitly (§136) | IMPLEMENTED |
| No fabricated AI outputs or thresholds (§75, §76) | IMPLEMENTED |

---

## 4. Naming (§150, §151)

Migrated deliberately, not by global replacement, as §151 requires.

| Surface | State |
| --- | --- |
| Product branding, API title, docs | EagleEye |
| New buckets | `eagleeye-media`, `eagleeye-derived`, `eagleeye-face-reference`, `eagleeye-face-observations` |
| Container names | `eagleeye-*` |
| `FACEID_` env prefix | Legacy, retained — renaming breaks every deployment `.env` |
| `hawkeye-api:dev` image tag, `hawkeye` service name | Legacy, retained — renaming breaks CI/CD and GHCR pull paths |
| Database name `faceid` | Legacy, retained — renaming is a migration with no benefit |

Legacy identifiers are compatibility names and are documented as such. No new
code introduces them.

---

## 5. Honest scope statement

EagleEye today is a **verified face-identity platform with a durable processing
core, a first-class media subsystem and a Dhivehi language slice.**

It is **not yet** the multimodal intelligence platform §1 describes. Of the 28
capabilities listed in §1, 5 are implemented (Face Intelligence, Face Search,
Semantic Search, Audit Logging, Analyst Review — the last two for the face
domain only). The evidence model of §50–§58, which §158 calls more important
than any individual model, does not exist yet.

`WHATS_MISSING.md` is the register of the rest.
