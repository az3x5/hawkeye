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

/**
 * Whether the session cookie is marked Secure.
 *
 * This must follow the transport, not the build mode. A Secure cookie is
 * discarded outright by the browser over plain HTTP on any origin except
 * localhost — so tying it to NODE_ENV meant a production build served over
 * HTTP silently dropped every session and the sign-in page simply reappeared.
 *
 * Defaults to true. Setting HAWKEYE_COOKIE_SECURE=false is only defensible
 * when something else is encrypting the transport — a WireGuard or Tailscale
 * network, or a TLS-terminating proxy in front. On an open network it puts the
 * session token on the wire in clear.
 */
export function cookieSecure(): boolean {
  return process.env.HAWKEYE_COOKIE_SECURE !== "false";
}

/** Cookie options: not readable by scripts, not sent cross-site. */
export function cookieOptions(secure: boolean = cookieSecure()) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    secure,
    path: "/",
    maxAge: 60 * 60 * 8,
  };
}
