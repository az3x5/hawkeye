/**
 * Types mirroring the Face ID API's response schemas.
 *
 * Scores are raw cosine similarities in [-1, 1]. They are not probabilities
 * and must never be rendered as percentages or as "confidence".
 */

export type DecisionOutcome = "accept" | "review" | "reject";
export type ReviewOutcome = "confirmed" | "rejected";

export interface Thresholds {
  accept_at: number;
  review_at: number;
  policy_version: string;
}

export interface Candidate {
  person_uuid: string;
  face_sample_uuid: string;
  score: number;
  sample_count: number;
}

export interface Identification {
  identification_uuid: string;
  outcome: DecisionOutcome;
  thresholds: Thresholds;
  candidates: Candidate[];
  margin: number | null;
  review_outcome: ReviewOutcome | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
}

export interface IdentificationSummary {
  identification_uuid: string;
  outcome: DecisionOutcome;
  created_at: string;
  best_person_uuid: string | null;
  best_score: number | null;
  margin: number | null;
  candidate_count: number;
  policy_version: string;
}

export interface ReviewQueue {
  items: IdentificationSummary[];
  count: number;
}

export interface Identity {
  subject: string;
  kind: string;
  scopes: string[];
  token_uuid: string;
  expires_at: string | null;
}

export interface DependencyStatus {
  name: string;
  healthy: boolean;
  error: string | null;
}

export interface Readiness {
  status: "ready" | "not_ready";
  checks: DependencyStatus[];
}

export interface Health {
  status: "ok";
  service: string;
  environment: string;
}

export interface Account {
  user_uuid: string;
  email: string;
  scopes: string[];
  active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenRecord {
  token_uuid: string;
  subject: string;
  kind: string;
  scopes: string[];
  active: boolean;
  expired: boolean;
  created_at: string;
  expires_at: string | null;
}

/** A newly issued credential. The secret is present only in this response. */
export interface IssuedToken extends TokenRecord {
  token: string;
}

/** The scopes the API recognises. */
export const SCOPES = ["enrol", "identify", "review", "admin"] as const;
export type Scope = (typeof SCOPES)[number];

/** The API's structured error envelope. */
export interface ApiErrorDetail {
  code: string;
  message: string;
  field: string | null;
}

export interface ApiErrorBody {
  error: ApiErrorDetail;
  details: ApiErrorDetail[];
}
