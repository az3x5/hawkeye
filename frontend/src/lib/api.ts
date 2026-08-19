/**
 * Server-side client for the Face ID API.
 *
 * Only ever called from server components and route handlers: the API is on an
 * internal network and is never exposed to the reviewer's browser.
 */

import { readToken } from "./session";
import type {
  ApiErrorBody,
  Identification,
  Identity,
  ReviewOutcome,
  ReviewQueue,
} from "./types";

const BASE_URL = process.env.FACEID_API_URL ?? "http://127.0.0.1:8000";

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

  const response = await fetch(`${BASE_URL}${path}`, {
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
  return (await response.json()) as T;
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
  const response = await fetch(`${BASE_URL}/api/v1/sessions`, {
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
  await fetch(`${BASE_URL}/api/v1/sessions/current`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  }).catch(() => undefined);
}
