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
