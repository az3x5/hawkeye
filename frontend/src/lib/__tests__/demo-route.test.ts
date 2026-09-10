import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/lib/session", () => ({ readToken: vi.fn(async () => "test-session") }));
import { POST } from "@/app/api/demo/route";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it("accepts the browser host when the framework uses an internal request URL", async () => {
  vi.stubEnv("FACEID_MEDIA_DEMO_URL", "http://media-demo:8020");
  const fetcher = vi.fn(async () => Response.json({ id: "job" }, { status: 202 }));
  vi.stubGlobal("fetch", fetcher);
  const result = await POST(new Request("http://localhost:3000/api/demo", { method: "POST", headers: { host: "demo.example:3100", origin: "http://demo.example:3100" }, body: "sample=true" }));
  expect(result.status).toBe(202);
  expect(fetcher).toHaveBeenCalledOnce();
});

it("rejects a cross-origin submission before contacting inference", async () => {
  vi.stubEnv("FACEID_MEDIA_DEMO_URL", "http://media-demo:8020");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const result = await POST(new Request("http://localhost:3000/api/demo", { method: "POST", headers: { host: "demo.example", origin: "https://other.example" }, body: "sample=true" }));
  expect(result.status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
