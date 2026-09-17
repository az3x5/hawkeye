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
  /^media$/,
  /^integrations\/blackglass\/capabilities$/,
  /^integrations\/blackglass\/documents$/,
  /^integrations\/blackglass\/evidence$/,
  /^integrations\/blackglass\/evidence\/[0-9a-f-]{36}(\/(content|report))?$/,
  /^integrations\/blackglass\/evidence\/events$/,
  /^integrations\/blackglass\/evidence\/status$/,
  /^integrations\/blackglass\/evidence-search$/,
];

const WRITABLE = [
  /^identifications\/[0-9a-f-]{36}\/review$/,
  /^nlp\/speech\/transcribe$/,
  /^nlp\/ocr$/,
  /^media$/,
  /^integrations\/blackglass\/(media|text)$/,
  /^integrations\/blackglass\/profiles\/[^/]+\/analyze$/,
  /^integrations\/blackglass\/evidence\/(media|text)$/,
];

const MAX_BROWSER_MEDIA_BYTES = 50 * 1024 * 1024;

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

  const isBrowserMedia = path === "media" || path === "integrations/blackglass/media" || path.startsWith("integrations/blackglass/evidence/");
  if (request.method === "POST" && isBrowserMedia) {
    let sameHost = false;
    try {
      const origin = new URL(request.headers.get("origin") ?? "");
      sameHost = ["http:", "https:"].includes(origin.protocol) && origin.host === request.headers.get("host");
    } catch {
      // Browser media submissions must carry a valid same-origin Origin header.
    }
    if (!sameHost) {
      return NextResponse.json(
        { error: { code: "origin_rejected", message: "Submit from this application.", field: null }, details: [] },
        { status: 403 },
      );
    }
    const declaredLength = Number(request.headers.get("content-length") ?? 0);
    if (Number.isFinite(declaredLength) && declaredLength > MAX_BROWSER_MEDIA_BYTES) {
      return NextResponse.json(
        { error: { code: "media_too_large", message: "Choose a file below 50 MB.", field: "file" }, details: [] },
        { status: 413 },
      );
    }
  }

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

  const isUpload = path === "nlp/speech/transcribe" || path === "nlp/ocr" || isBrowserMedia;
  const contentType = request.headers.get("content-type");
  const requestUrl = new URL(request.url);
  const uploadBody = request.method === "POST" && isUpload ? await request.arrayBuffer() : null;
  if (isBrowserMedia && uploadBody !== null && uploadBody.byteLength > MAX_BROWSER_MEDIA_BYTES) {
    return NextResponse.json(
      { error: { code: "media_too_large", message: "Choose a file below 50 MB.", field: "file" }, details: [] },
      { status: 413 },
    );
  }
  const upstream = await fetch(`${apiBaseUrl()}/api/v1/${path}${request.method === "GET" ? requestUrl.search : ""}`, {
    method: request.method,
    headers:
      request.method === "POST"
        ? {
            ...(contentType === null ? {} : { "Content-Type": contentType }),
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
          }
        : {
            Accept: path === "system/metrics" || path === "statistics" || path === "media" || path === "integrations/blackglass/capabilities" || path === "integrations/blackglass/documents" ? "application/json" : "image/jpeg",
            Authorization: `Bearer ${token}`,
          },
    body:
      request.method === "POST"
        ? isUpload
          ? uploadBody
          : await request.text()
        : undefined,
    cache: "no-store",
  });

  const headers = new Headers();
  const upstreamContentType = upstream.headers.get("content-type");
  if (upstreamContentType !== null) headers.set("content-type", upstreamContentType);
  const disposition = upstream.headers.get("content-disposition");
  if (disposition !== null) headers.set("content-disposition", disposition);
  headers.set("x-content-type-options", "nosniff");
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
