/**
 * Types mirroring the Hawkeye API's response schemas.
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

export interface CapacityUsage {
  used_bytes: number;
  total_bytes: number;
  percent: number;
}

export interface GpuUsage {
  name: string;
  utilization_percent: number;
  memory: CapacityUsage;
}

export interface SystemMetrics {
  collected_at: string;
  cpu_percent: number;
  cpu_count: number;
  memory: CapacityUsage;
  disk: CapacityUsage;
  gpus: GpuUsage[];
  gpu_error: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PersonIdentifier {
  source: string;
  kind: string;
  value: string;
}

export interface PersonSummary {
  person_uuid: string;
  created_at: string;
  sample_count: number;
  processed_count: number;
  identifiers: PersonIdentifier[];
}

export interface PersonDetail extends PersonSummary {
  samples: FaceSample[];
}

export interface IdentificationRecord {
  identification_uuid: string;
  outcome: DecisionOutcome;
  policy_version: string;
  accept_at: number;
  review_at: number;
  best_person_uuid: string | null;
  best_score: number | null;
  created_at: string;
  review_outcome: ReviewOutcome | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  candidates: Candidate[];
}

export interface AuditEvent {
  audit_uuid: string;
  occurred_at: string;
  action: string;
  actor_identifier: string;
  actor_kind: string;
  person_uuid: string | null;
  face_sample_uuid: string | null;
  identification_uuid: string | null;
  policy_version: string | null;
  details: Record<string, unknown>;
}

export interface AuditPage extends Paged<AuditEvent> {
  actions: string[];
}

export interface Statistics {
  persons: number;
  face_samples: number;
  samples_by_state: Record<string, number>;
  embeddings: number;
  identifications: number;
  identifications_by_outcome: Record<string, number>;
  awaiting_review: number;
  reviews_recorded: number;
  audit_events: number;
}

export type ProcessingState = "pending" | "processed" | "failed";

export interface FaceSample {
  face_sample_uuid: string;
  person_uuid: string;
  source: string;
  image_sha256: string;
  processing_state: ProcessingState;
  captured_at: string | null;
  created_at: string;
  processed_at: string | null;
  failure_reason: string | null;
}

export interface Enrolment {
  person_uuid: string;
  sample: FaceSample;
  /** False when this submission repeated an earlier one. */
  created: boolean;
  status: "accepted" | "already_enrolled";
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
export const SCOPES = ["enrol", "identify", "review", "language", "admin"] as const;
export type Scope = (typeof SCOPES)[number];

export type LanguageScript = "thaana" | "latin" | "number" | "other";
export type PrimaryScript = "thaana" | "latin" | "mixed" | "none";
export type TransliterationDirection = "latin_to_thaana" | "thaana_to_latin";

export interface LanguageScriptSpan {
  text: string;
  start: number;
  end: number;
  script: LanguageScript;
}

export interface LanguageNormalization {
  original: string;
  normalized: string;
  primary_script: PrimaryScript;
  spans: LanguageScriptSpan[];
  normalizer_version: string;
}

export interface LanguageTransliteration {
  original: string;
  output: string;
  direction: TransliterationDirection;
  model_version: string;
  warnings: string[];
}

export type LanguageDocumentState = "pending" | "processed" | "failed";

export interface LanguageDocument {
  document_uuid: string;
  title: string;
  source: string;
  original_text: string;
  normalized_text: string;
  primary_script: PrimaryScript;
  content_sha256: string;
  attributes: Record<string, unknown>;
  processing_state: LanguageDocumentState;
  embedding_model: string | null;
  embedding_version: string | null;
  vector_collection: string | null;
  failure_reason: string | null;
  created_at: string;
  processed_at: string | null;
  created: boolean | null;
  status: "accepted" | "already_exists" | null;
}

export interface LanguageSearchHit {
  document_uuid: string;
  title: string;
  source: string;
  text: string;
  primary_script: PrimaryScript;
  score: number;
  attributes: Record<string, unknown>;
}

export interface LanguageSearchResponse {
  query: string;
  model: string;
  model_version: string;
  hits: LanguageSearchHit[];
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
