/**
 * Runtime proxy to the Face ID API.
 *
 * A build-time `rewrites()` rule bakes the destination into the build output,
 * so it cannot follow an environment variable set at deploy time. This handler
 * reads the address per request instead.
 *
 * It is also an allowlist rather than a passthrough: only the two image reads
 * and the review write a browser legitimately needs are forwarded. Enrolment,
 * identification and the queue are server-rendered and never travel through
 * here, so exposing them to the browser would widen the surface for nothing.
 */

import { NextResponse } from "next/server";
import { readToken } from "@/lib/session";

const BASE_URL = () => process.env.FACEID_API_URL ?? "http://127.0.0.1:8000";

const READABLE = [
  /^identifications\/[0-9a-f-]{36}\/image$/,
  /^face-samples\/[0-9a-f-]{36}\/image$/,
];

const WRITABLE = [/^identifications\/[0-9a-f-]{36}\/review$/];

function refuse(): NextResponse {
  return NextResponse.json(
    {
      error: {
        code: "not_proxied",
        message: "This path is not available through the review app.",
        field: null,
      },
      details: [],
    },
    { status: 404 },
  );
}

async function forward(request: Request, path: string, allowed: RegExp[]): Promise<Response> {
  if (!allowed.some((pattern) => pattern.test(path))) return refuse();

  // The reviewer's own credential, never one belonging to this app: the API
  // attributes the action to them.
  const token = await readToken();
  if (token === null) {
    return NextResponse.json(
      {
        error: { code: "not_authenticated", message: "Sign in to continue.", field: null },
        details: [],
      },
      { status: 401 },
    );
  }

  const upstream = await fetch(`${BASE_URL()}/api/v1/${path}`, {
    method: request.method,
    headers:
      request.method === "POST"
        ? {
            "Content-Type": "application/json",
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
          }
        : { Accept: "image/jpeg", Authorization: `Bearer ${token}` },
    body: request.method === "POST" ? await request.text() : undefined,
    cache: "no-store",
  });

  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType !== null) headers.set("content-type", contentType);
  // Biometric images must not linger in shared caches.
  headers.set("cache-control", "private, no-store");

  return new NextResponse(upstream.body, { status: upstream.status, headers });
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await params;
  return forward(request, path.join("/"), READABLE);
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await params;
  return forward(request, path.join("/"), WRITABLE);
}
