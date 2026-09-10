import { readToken } from "@/lib/session";

export const dynamic = "force-dynamic";
const LIMIT = 21 * 1024 * 1024;

async function forward(request: Request) {
  const token = await readToken();
  if (!token) return Response.json({ detail: "Sign in to continue." }, { status: 401 });
  const base = process.env.FACEID_MEDIA_DEMO_URL;
  if (!base) return Response.json({ detail: "Media demonstration service is not configured." }, { status: 503 });
  if (request.method === "POST" && request.headers.get("origin") !== new URL(request.url).origin) {
    return Response.json({ detail: "Submit from this application." }, { status: 403 });
  }
  let body: Uint8Array<ArrayBuffer> | undefined;
  if (request.method === "POST") {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > LIMIT) {
          await reader.cancel();
          return Response.json({ detail: "Choose a file below 20 MB." }, { status: 413 });
        }
        chunks.push(value);
      }
    }
    body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  }
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/jobs`, {
      method: request.method,
      headers: { Authorization: `Bearer ${token}`, ...(request.method === "POST" ? { "Content-Type": request.headers.get("content-type") ?? "application/octet-stream" } : {}) },
      body, cache: "no-store", signal: AbortSignal.timeout(30000),
    });
    return new Response(response.body, { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" } });
  } catch {
    return Response.json({ detail: "Media service is unavailable. Try again shortly." }, { status: 503 });
  }
}
export const GET = forward;
export const POST = forward;
