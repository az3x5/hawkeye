import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/session";

/** Clear the reviewer's session. */
export async function POST(request: Request): Promise<Response> {
  const response = NextResponse.redirect(new URL("/sign-in", request.url), { status: 303 });
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
