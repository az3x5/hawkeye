/**
 * Runtime proxy to the Hawkeye API.
 *
 * A build-time `rewrites()` rule bakes the destination into the build output,
 * so it cannot follow an environment variable set at deploy time. This handler
 * reads the address per request instead.
 *
 * It is also an allowlist rather than a passthrough: only image reads, live
 * system metrics and the review write a browser legitimately needs are
 * forwarded. Enrolment, identification and the queue are server-rendered and
 * never travel through here, so exposing them to the browser would widen the
 * surface for nothing.
 */

import { NextResponse } from "next/server";
import { apiBaseUrl } from "@/lib/config";
import { readToken } from "@/lib/session";

// Address from configuration, per request. No fallback host: a silent
// default would send biometric traffic somewhere nobody chose.

const READABLE = [
  /^identifications\/[0-9a-f-]{36}\/image$/,
  /^face-samples\/[0-9a-f-]{36}\/image$/,
  /^statistics$/,
  /^system\/metrics$/,
];

const WRITABLE = [
  /^identifications\/[0-9a-f-]{36}\/review$/,
  /^nlp\/speech\/transcribe$/,
  /^nlp\/ocr$/,
];

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

  const isUpload = path === "nlp/speech/transcribe" || path === "nlp/ocr";
  const contentType = request.headers.get("content-type");
  const upstream = await fetch(`${apiBaseUrl()}/api/v1/${path}`, {
    method: request.method,
    headers:
      request.method === "POST"
        ? {
            ...(contentType === null ? {} : { "Content-Type": contentType }),
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
          }
        : {
            Accept: path === "system/metrics" || path === "statistics" ? "application/json" : "image/jpeg",
            Authorization: `Bearer ${token}`,
          },
    body:
      request.method === "POST"
        ? isUpload
          ? await request.arrayBuffer()
          : await request.text()
        : undefined,
    cache: "no-store",
  });

  const headers = new Headers();
  const upstreamContentType = upstream.headers.get("content-type");
  if (upstreamContentType !== null) headers.set("content-type", upstreamContentType);
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
