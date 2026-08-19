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

## Later phases

Sequenced by backend readiness rather than by preference.

| Phase | Scope | Status |
| --- | --- | --- |
| UI-01 | Review workspace polish: queue filters, keyboard triage across items | ⬜ NOT STARTED — backend ready |
| UI-02 | Identify screen: submit an image, show the decision and candidates | ⬜ NOT STARTED — backend ready |
| UI-03 | Settings: account, password change, credential administration | ⬜ NOT STARTED — backend ready |
| UI-04 | Enrollments: submit and track a sample | ⬜ NOT STARTED — partial; needs a sample list endpoint for the register |
| UI-05 | Matches: identification history | 🚫 BLOCKED — needs a history endpoint beyond the review queue |
| UI-06 | Persons: browse people and their samples | 🚫 BLOCKED — needs person list and read endpoints |
| UI-07 | Audit: view the audit log | 🚫 BLOCKED — needs a read endpoint for `audit_events` |
| UI-08 | Dashboard: operational figures | 🚫 BLOCKED — needs an aggregate metrics endpoint |

Blocked phases require backend work first. Building them now would mean
inventing data, which is the one thing this interface must not do.
