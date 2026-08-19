/**
 * Reviewer session.
 *
 * The reviewer's own API token is held in an httpOnly cookie, so it is never
 * readable by page scripts, and every request is attributed to *them* rather
 * than to a shared credential belonging to this app. That attribution is the
 * whole point: the audit log records who decided.
 */

import { cookies } from "next/headers";

export const SESSION_COOKIE = "faceid_token";

/** Read the reviewer's token, or null when they are not signed in. */
export async function readToken(): Promise<string | null> {
  const store = await cookies();
  const value = store.get(SESSION_COOKIE)?.value;
  return value === undefined || value.trim() === "" ? null : value;
}

/** Cookie options: not readable by scripts, not sent cross-site. */
export function cookieOptions(secure: boolean) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    secure,
    path: "/",
    maxAge: 60 * 60 * 8,
  };
}
