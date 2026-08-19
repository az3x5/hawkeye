import { NextResponse } from "next/server";
import { signOut } from "@/lib/api";
import { SESSION_COOKIE } from "@/lib/session";

/** Clear the session, revoking it server-side as well as dropping the cookie. */
export async function POST(request: Request): Promise<Response> {
  // Dropping the cookie alone would leave a working credential in existence.
  await signOut();
  const response = NextResponse.redirect(new URL("/sign-in", request.url), { status: 303 });
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
