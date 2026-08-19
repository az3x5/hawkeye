/**
 * Server-side client for the Face ID API.
 *
 * Only ever called from server components and route handlers: the API is on an
 * internal network and is never exposed to the reviewer's browser.
 */

import { apiBaseUrl } from "./config";
import { readToken } from "./session";
import type {
  Account,
  ApiErrorBody,
  Enrolment,
  FaceSample,
  Health,
  IssuedToken,
  Readiness,
  TokenRecord,
  Identification,
  Identity,
  ReviewOutcome,
  ReviewQueue,
} from "./types";

// Read per call rather than at import: a module should not refuse to load
// because configuration is absent, and tests import this without an API.

/** An error carrying the API's structured code, so callers can branch on it. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorBody).error?.code === "string"
  );
}

export class NotAuthenticatedError extends Error {
  constructor() {
    super("Sign in with your review token to continue.");
    this.name = "NotAuthenticatedError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await readToken();
  if (token === null) throw new NotAuthenticatedError();

  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
    // A review queue that shows stale state is worse than a slow one.
    cache: "no-store",
  });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    if (isErrorBody(body)) {
      throw new ApiError(response.status, body.error.code, body.error.message);
    }
    throw new ApiError(response.status, "unexpected_error", `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Readiness of the API and its dependencies.
 *
 * Needs no credential, and returns null rather than throwing when the API
 * cannot be reached at all — "unreachable" is a status worth displaying, not
 * an error that should blank the page.
 */
export async function fetchReadiness(): Promise<Readiness | null> {
  try {
    const response = await fetch(`${apiBaseUrl()}/api/v1/readyz`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    // 503 is a real answer: it carries which dependency is failing.
    if (response.status !== 200 && response.status !== 503) return null;
    return (await response.json()) as Readiness;
  } catch {
    return null;
  }
}

/** Liveness of the API process. */
export async function fetchHealth(): Promise<Health | null> {
  try {
    const response = await fetch(`${apiBaseUrl()}/api/v1/health`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    return response.ok ? ((await response.json()) as Health) : null;
  } catch {
    return null;
  }
}

/** Who the signed-in reviewer is. */
export function fetchIdentity(): Promise<Identity> {
  return request<Identity>("/api/v1/me");
}

/** Proposals waiting for a human, oldest first. */
export function fetchReviewQueue(limit = 50): Promise<ReviewQueue> {
  return request<ReviewQueue>(`/api/v1/identifications?limit=${limit}`);
}

/** One identification, including any review already recorded. */
export function fetchIdentification(id: string): Promise<Identification> {
  return request<Identification>(`/api/v1/identifications/${id}`);
}

/** Record a reviewer's conclusion. */
export function submitReview(
  id: string,
  body: { outcome: ReviewOutcome; note?: string },
): Promise<Identification> {
  return request<Identification>(`/api/v1/identifications/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Exchange an email and password for a session credential.
 *
 * Called from the sign-in server action, which is the only place without a
 * session yet, so it bypasses the usual token-attaching request helper.
 */
export async function signIn(
  email: string,
  password: string,
): Promise<{ token: string; subject: string }> {
  const response = await fetch(`${apiBaseUrl()}/api/v1/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    if (isErrorBody(body)) throw new ApiError(response.status, body.error.code, body.error.message);
    throw new ApiError(response.status, "unexpected_error", `HTTP ${response.status}`);
  }
  return (await response.json()) as { token: string; subject: string };
}

/** Revoke the current session server-side, so signing out really ends it. */
export async function signOut(): Promise<void> {
  const token = await readToken();
  if (token === null) return;
  await fetch(`${apiBaseUrl()}/api/v1/sessions/current`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  }).catch(() => undefined);
}

/**
 * Submit an image for identification.
 *
 * Multipart rather than JSON, and sent from the server so the reviewer's
 * credential never reaches the browser. The image itself is forwarded straight
 * through and not retained here.
 */
export async function submitIdentification(image: File): Promise<Identification> {
  const token = await readToken();
  if (token === null) throw new NotAuthenticatedError();

  const body = new FormData();
  body.append("image", image);

  const response = await fetch(`${apiBaseUrl()}/api/v1/identifications`, {
    method: "POST",
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    body,
    cache: "no-store",
  });

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    if (isErrorBody(payload)) {
      throw new ApiError(response.status, payload.error.code, payload.error.message);
    }
    throw new ApiError(response.status, "unexpected_error", `HTTP ${response.status}`);
  }
  return (await response.json()) as Identification;
}

// ---------------------------------------------------------------------------
// Account and credential administration
// ---------------------------------------------------------------------------

/** Every password account. Requires the `admin` scope. */
export function fetchAccounts(): Promise<Account[]> {
  return request<Account[]>("/api/v1/accounts");
}

/** Create a password account. Requires the `admin` scope. */
export function createAccount(body: {
  email: string;
  password: string;
  scopes: string[];
}): Promise<Account> {
  return request<Account>("/api/v1/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Set somebody else's password. Requires the `admin` scope. */
export function setAccountPassword(userUuid: string, password: string): Promise<Account> {
  return request<Account>(`/api/v1/accounts/${userUuid}/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

/** Suspend or restore an account. Requires the `admin` scope. */
export function setAccountDisabled(userUuid: string, disabled: boolean): Promise<Account> {
  return request<Account>(`/api/v1/accounts/${userUuid}/${disabled ? "disable" : "enable"}`, {
    method: "POST",
  });
}

/** Change your own password. Every session, including this one, is revoked. */
export async function changeOwnPassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await request<void>("/api/v1/me/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

/** Every issued credential. Secrets are not recoverable, only metadata. */
export function fetchTokens(): Promise<TokenRecord[]> {
  return request<TokenRecord[]>("/api/v1/tokens");
}

/** Issue a credential. The secret comes back once and is never stored. */
export function issueToken(body: {
  subject: string;
  kind: string;
  scopes: string[];
  expires_in_days?: number | null;
  never_expires?: boolean;
}): Promise<IssuedToken> {
  return request<IssuedToken>("/api/v1/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Revoke a credential. Takes effect on its next request. */
export async function revokeToken(tokenUuid: string): Promise<void> {
  await request<void>(`/api/v1/tokens/${tokenUuid}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Enrolment
// ---------------------------------------------------------------------------

/**
 * Register a face against a person.
 *
 * Idempotent at the API: the same image under the same identifiers returns the
 * original person and sample and schedules no further work, which the caller
 * can tell from `created`.
 */
export async function submitEnrolment(fields: {
  source: string;
  externalId: string | null;
  localId: string | null;
  capturedAt: string | null;
  image: File;
}): Promise<Enrolment> {
  const token = await readToken();
  if (token === null) throw new NotAuthenticatedError();

  const body = new FormData();
  body.append("source", fields.source);
  body.append("image", fields.image);
  if (fields.externalId) body.append("external_id", fields.externalId);
  if (fields.localId) body.append("local_id", fields.localId);
  if (fields.capturedAt) body.append("captured_at", fields.capturedAt);

  const response = await fetch(`${apiBaseUrl()}/api/v1/enrolments`, {
    method: "POST",
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    body,
    cache: "no-store",
  });

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    if (isErrorBody(payload)) {
      throw new ApiError(response.status, payload.error.code, payload.error.message);
    }
    throw new ApiError(response.status, "unexpected_error", `HTTP ${response.status}`);
  }
  return (await response.json()) as Enrolment;
}

/** One face sample, including how far it has got through the pipeline. */
export function fetchFaceSample(faceSampleUuid: string): Promise<FaceSample> {
  return request<FaceSample>(`/api/v1/face-samples/${faceSampleUuid}`);
}
