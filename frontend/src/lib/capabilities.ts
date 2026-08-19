/**
 * What the backend can actually do today.
 *
 * Each screen declares the API capability it needs. Where that capability does
 * not exist, the screen says so plainly instead of rendering plausible-looking
 * numbers — a dashboard of invented metrics is worse than an empty one,
 * because it cannot be told apart from a working system.
 *
 * Verified against the API's own OpenAPI document, not assumed.
 */

export type CapabilityState = "available" | "partial" | "unavailable";

export interface Capability {
  state: CapabilityState;
  /** What the backend does support, if anything. */
  supported: string[];
  /** What is missing, in terms of endpoints rather than features. */
  missing: string[];
  /** Why the screen cannot be completed yet, in one sentence. */
  note: string;
}

export const CAPABILITIES = {
  dashboard: {
    state: "unavailable",
    supported: ["GET /health", "GET /readyz"],
    missing: ["an aggregate metrics or counts endpoint"],
    note:
      "The API exposes no counts, rates or totals. Operational figures would " +
      "have to be invented, so none are shown.",
  },
  identify: {
    state: "available",
    supported: ["POST /identifications", "GET /identifications/{uuid}"],
    missing: [],
    note: "Identification is fully supported; the screen is scheduled for UI-02.",
  },
  enrollments: {
    state: "partial",
    supported: [
      "POST /enrolments",
      "GET /face-samples/{uuid}",
      "GET /face-samples/{uuid}/image",
    ],
    missing: ["a list endpoint for enrolled samples"],
    note:
      "A sample can be created and read by identifier, but there is no way to " +
      "list them, so this screen cannot show an enrolment register yet.",
  },
  review: {
    state: "available",
    supported: [
      "GET /identifications",
      "GET /identifications/{uuid}",
      "POST /identifications/{uuid}/review",
      "GET /identifications/{uuid}/image",
    ],
    missing: [],
    note: "Fully supported and already implemented.",
  },
  persons: {
    state: "unavailable",
    supported: ["DELETE /persons/{uuid}"],
    missing: ["list and read endpoints for people and their samples"],
    note:
      "A person can be erased but not listed or read, so there is nothing to " +
      "browse. Erasure is available through the API.",
  },
  matches: {
    state: "partial",
    supported: ["GET /identifications (proposals awaiting review only)"],
    missing: ["a history endpoint covering accepted and rejected identifications"],
    note:
      "Only unreviewed proposals can be listed. Accepted and rejected " +
      "identifications are recorded but cannot be queried.",
  },
  audit: {
    state: "unavailable",
    supported: [],
    missing: ["a read endpoint for audit_events"],
    note:
      "Every administrative and review action is recorded, but the log is " +
      "only reachable from the database — nothing exposes it over HTTP.",
  },
  settings: {
    state: "available",
    supported: [
      "GET /me",
      "POST /me/password",
      "GET/POST /accounts",
      "GET/POST /tokens",
    ],
    missing: [],
    note: "Account and credential administration is supported; scheduled for UI-07.",
  },
} as const satisfies Record<string, Capability>;

export type CapabilityKey = keyof typeof CAPABILITIES;
