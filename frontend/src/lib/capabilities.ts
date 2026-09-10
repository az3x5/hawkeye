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


export const CAPABILITIES: Record<CapabilityKey, Capability> = {
  dashboard: {
    state: "partial",
    supported: ["GET /statistics", "GET /health", "GET /readyz"],
    missing: ["a time series, for rates and trends rather than totals"],
    note:
      "Counts are real and come from the API. Rates and trends are not shown " +
      "because the system keeps no history to derive them from.",
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
    state: "available",
    supported: [
      "GET /persons",
      "GET /persons/{uuid}",
      "DELETE /persons/{uuid}",
    ],
    missing: [],
    note: "People can be listed, read and erased.",
  },
  matches: {
    state: "available",
    supported: ["GET /identification-history", "GET /identifications/{uuid}"],
    missing: [],
    note: "Every identification can be listed and filtered.",
  },
  vision: {
    state: "partial",
    supported: ["GET/POST /media-demo/jobs", "Qwen vision and bounded media analysis"],
    missing: ["continuous object tracking and identity association"],
    note: "Uploaded media analysis is live. Installed specialist models are reported by the running service; continuous tracking is not yet connected.",
  },
  language: {
    state: "available",
    supported: ["POST /nlp/normalize", "POST /nlp/transliterate"],
    missing: [],
    note: "Unicode normalization, script detection and rule transliteration are available.",
  },
  audit: {
    state: "available",
    supported: ["GET /audit-events"],
    missing: [],
    note: "The append-only log is readable. Nothing can edit or remove an event.",
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
};

export type CapabilityKey =
  | "dashboard"
  | "identify"
  | "enrollments"
  | "review"
  | "persons"
  | "matches"
  | "vision"
  | "language"
  | "audit"
  | "settings";
