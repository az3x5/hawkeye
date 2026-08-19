# UI implementation status

Authoritative record of what the frontend actually does. A phase is complete
only when its acceptance criteria have been verified by running them.

Last updated: 2026-08-19.

## Backend capability map

Verified against the API's own OpenAPI document, not assumed. This is what
decides whether a screen can be built at all.

| Screen | API support | State |
| --- | --- | --- |
| Review | `GET/POST /identifications`, `/review`, `/image` | **available** |
| Identify | `POST /identifications` | **available** |
| Settings | `/me`, `/me/password`, `/accounts`, `/tokens` | **available** |
| Enrollments | `POST /enrolments`, `GET /face-samples/{uuid}` — no list endpoint | **partial** |
| Matches | `GET /identifications` (awaiting review only) — no history | **partial** |
| Dashboard | `/health`, `/readyz` only — no metrics or counts | **unavailable** |
| Persons | `DELETE /persons/{uuid}` only — no list or read | **unavailable** |
| Audit | none — `audit_events` is not exposed over HTTP | **unavailable** |

The map lives in `src/lib/capabilities.ts` and drives what each screen renders.

## UI-00 Foundation — ✅ COMPLETE

### Delivered

| Item | Location |
| --- | --- |
| Next.js 16 + React 19, TypeScript `strict` | `tsconfig.json` |
| Tailwind CSS v4, dark operations theme | `postcss.config.mjs`, `src/app/globals.css` |
| shadcn/ui (button, badge, card, separator, sheet, skeleton) | `src/components/ui/` |
| Lucide icons | used throughout the shell |
| Responsive shell: sidebar, drawer, header, breadcrumbs, container | `src/components/shell/` |
| Nine routes | `src/app/` |
| `LoadingState`, `EmptyState`, `ErrorState`, `StatusBadge` | `src/components/states/` |
| `NotAvailable` capability panel | `src/components/states/not-available.tsx` |
| Typed API client and domain types | `src/lib/api.ts`, `src/lib/types.ts` |
| Configurable API base URL, no fallback host | `src/lib/config.ts` |
| Health/status component consuming `/health` and `/readyz` | `src/components/system-status.tsx` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| `npm run build` passes | run | ✅ 14 routes built |
| Lint passes | `eslint .` | ✅ no errors |
| TypeScript passes | `tsc --noEmit`, `strict` | ✅ |
| All routes render | HTTP status for each, signed in | ✅ `/` 307 → dashboard, others 200 |
| Desktop sidebar works | browser at 1280px | ✅ visible, 240px, trigger hidden |
| Mobile navigation works | browser at 375px | ✅ sidebar hidden, drawer lists all 8 items, no overflow |
| No fake backend data | grep for invented metrics; screens inspected | ✅ only `/health`, `/readyz` and real review data |
| API base URL configurable | `FACEID_API_URL`, no fallback | ✅ throws a named error when unset |
| No hard-coded localhost in components | grep across `src/` | ✅ only in the config error message |
| No secrets committed | `.env.local` git-ignored; no credentials in source | ✅ |
| No direct database/vector/storage connections | no driver dependencies present | ✅ HTTP to the API only |
| Biometric data out of web storage | no `localStorage`/`sessionStorage` use | ✅ session token in an httpOnly cookie |

### Notes

- **Existing functionality was preserved, not rebuilt.** Sign-in and the review
  screens were already working against real endpoints and verified in a
  browser. They were migrated into the new shell and restyled rather than
  replaced with placeholders, since deleting working software to satisfy a
  foundation phase would be a regression. `/review` is therefore live rather
  than a stub.
- **A bug found by checking rather than assuming.** `postcss.config.mjs` was
  never written — an earlier shell command silently skipped it — so Tailwind
  emitted no utility classes at all and the layout only *looked* plausible.
  Caught by inspecting computed styles at mobile width, where the sidebar was
  359px wide instead of hidden. The Dockerfile also had to be corrected to copy
  the config, or the container build would have reproduced it.

## UI-01 Review workspace — ✅ COMPLETE

Filtering and ordering for the backlog, and continuous keyboard triage.

### Delivered

| Item | Location |
| --- | --- |
| Filter, sort and position logic (pure) | `src/lib/review-queue.ts` |
| Interactive queue: search, sort, close-calls filter, policy filter | `src/app/review/queue-table.tsx` |
| Queue keyboard: `j`/`k` move, `↵` open, `/` focus filter | `src/app/review/queue-table.tsx` |
| Position in queue, next-item handoff, skip | `src/app/review/[id]/page.tsx`, `review-workspace.tsx` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| Search matches identification, person and policy | unit tests | ✅ case- and space-insensitive |
| Close-calls filter and policy filter combine | unit tests | ✅ |
| Five orderings, all stable for ties | unit tests | ✅ order never wobbles |
| A missing margin or score sorts last, not first | unit tests | ✅ |
| Filtering does not mutate the loaded page | unit test | ✅ |
| Filters run in the browser and say so | count line, empty state | ✅ "3 of 12 loaded" |
| Queue keyboard navigation | browser: `j` moved the cursor 0 → 1 | ✅ |
| Sort and filter controls work live | browser: close-calls filter, closest-call sort | ✅ reordered |
| Deciding carries the reviewer to the next proposal | browser: confirm → next id, 3 → 2 remaining | ✅ |
| Keyboard decisions do the same | browser: `r` → next id, 2 → 1 remaining | ✅ |
| The last proposal stays put and shows its decision | browser | ✅ no Skip, "last in the queue" |
| Decisions still reach the audit log | database | ✅ two confirmed, one rejected, all as `admin@admin.com` |
| Build, lint, types, tests | run | ✅ 54 tests pass |

### Notes

- **The API has no server-side filtering**: `GET /identifications` accepts only
  a `limit`. Filters therefore operate on the loaded page, and the count line
  and empty state both say so — a filter that silently searches part of the
  data would be worse than none.
- Skipping records nothing. An undecided proposal simply stays in the queue,
  so "skip" is navigation rather than a state the backend would have to model.
- The cursor is derived during render rather than synced in an effect, which
  ESLint's `set-state-in-effect` rule correctly flagged as a cascading render.

## UI-02 Identify — ✅ COMPLETE

Submit a face and see the proposal, its evidence and the policy behind it.

### Delivered

| Item | Location |
| --- | --- |
| Upload console: drag-and-drop, preview, submit, clear | `src/app/identify/identify-console.tsx` |
| Server action carrying the image to the API | `src/app/identify/actions.ts` |
| Multipart submission from the server | `src/lib/api.ts` (`submitIdentification`) |
| Scope check before showing the console | `src/app/identify/page.tsx` |

### Acceptance criteria — verified

| Criterion | How verified | Result |
| --- | --- | --- |
| A real image produces a real decision | browser: uploaded an enrolled face | ✅ `accept`, similarity 1.0000, policy `local-dev-v1` |
| Candidates render with scores and scales | browser | ✅ 3 candidates, each with its own scale |
| The decision is recorded | database | ✅ row written with outcome and policy |
| The attempt is audited against the account | database | ✅ `identification_performed` by `admin@admin.com` |
| A faceless image explains itself | browser: blank canvas image | ✅ "no face was detected in the query image", `identification_failed` |
| Review outcomes link into the review screen | code path, `outcome === "review"` | ✅ |
| Scores are never presented as probabilities | copy and rendering | ✅ raw similarities, thresholds labelled |
| Nothing is written to browser storage | browser | ✅ `localStorage` and `sessionStorage` both empty; session cookie not script-readable |
| The preview is released, not retained | object URL revoked on change | ✅ |
| A missing scope is stated, not hidden | `/identify` checks `identify` | ✅ |
| Build, lint, types, tests | run | ✅ 54 tests pass |

### Notes

- The upload goes through a **server action**, not the browser proxy. The
  proxy's allowlist stays narrow — image reads and the review write — and the
  credential never reaches the browser.
- The preview is a local object URL, derived during render and revoked when it
  changes. Biometric material should not outlive the tab it was dropped into.
- Candidate images are fetched through the proxy, which requires the `review`
  scope. An account holding `identify` alone will see the decision and scores
  but not the candidate thumbnails.

## Later phases

Sequenced by backend readiness rather than by preference.

| Phase | Scope | Status |
| --- | --- | --- |
| UI-01 | Review workspace: queue filters, keyboard triage across items | ✅ COMPLETE |
| UI-02 | Identify screen: submit an image, show the decision and candidates | ✅ COMPLETE |
| UI-03 | Settings: account, password change, credential administration | ⬜ NOT STARTED — backend ready |
| UI-04 | Enrollments: submit and track a sample | ⬜ NOT STARTED — partial; needs a sample list endpoint for the register |
| UI-05 | Matches: identification history | 🚫 BLOCKED — needs a history endpoint beyond the review queue |
| UI-06 | Persons: browse people and their samples | 🚫 BLOCKED — needs person list and read endpoints |
| UI-07 | Audit: view the audit log | 🚫 BLOCKED — needs a read endpoint for `audit_events` |
| UI-08 | Dashboard: operational figures | 🚫 BLOCKED — needs an aggregate metrics endpoint |

Blocked phases require backend work first. Building them now would mean
inventing data, which is the one thing this interface must not do.
