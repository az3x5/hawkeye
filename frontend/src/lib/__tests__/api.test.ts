import { afterEach, describe, expect, it, vi } from "vitest";

// The client reads its address from configuration, so the tests must supply one.
process.env.FACEID_API_URL = "http://api.test:8000";

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => ({ value: "faceid_test-token" }) }),
}));
import {
  accountIdentifier,
  ApiError,
  chatWithDhivehiBot,
  fetchIdentification,
  fetchReviewQueue,
  normalizeLanguageText,
  createLanguageDocument,
  searchLanguageDocuments,
  submitReview,
  transliterateLanguageText,
} from "../api";

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchReviewQueue", () => {
  it("requests the queue with a limit", async () => {
    const fetchMock = mockFetch(200, { items: [], count: 0 });
    await fetchReviewQueue(25);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/identifications?limit=25");
  });

  it("never serves a cached queue", async () => {
    const fetchMock = mockFetch(200, { items: [], count: 0 });
    await fetchReviewQueue();
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ cache: "no-store" });
  });
});

describe("error handling", () => {
  it("surfaces the API's structured code and message", async () => {
    mockFetch(409, {
      error: { code: "review_not_permitted", message: "already reviewed", field: null },
      details: [],
    });
    await expect(submitReview("abc", { outcome: "confirmed" })).rejects.toThrow(
      ApiError,
    );
    try {
      await submitReview("abc", { outcome: "confirmed" });
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).code).toBe("review_not_permitted");
      expect((error as ApiError).status).toBe(409);
      expect((error as ApiError).message).toBe("already reviewed");
    }
  });

  it("falls back when the body is not an error envelope", async () => {
    mockFetch(500, "gateway exploded");
    await expect(fetchIdentification("abc")).rejects.toMatchObject({
      code: "unexpected_error",
      status: 500,
    });
  });
});

describe("submitReview", () => {
  it("posts the reviewer and outcome as JSON", async () => {
    const fetchMock = mockFetch(200, {});
    await submitReview("abc", { outcome: "rejected", note: "not the same" });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      outcome: "rejected",
      note: "not the same",
    });
  });
});

describe("language API", () => {
  it("posts bounded bot history and an explicit response language", async () => {
    const fetchMock = mockFetch(200, { task: "understanding", text: "ރަނގަޅު" });

    await chatWithDhivehiBot([{ role: "user", content: "How are you?" }], "dhivehi");

    const call = fetchMock.mock.calls[0];
    expect(String(call?.[0])).toContain("/api/v1/nlp/chat");
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
      messages: [{ role: "user", content: "How are you?" }],
      response_language: "dhivehi",
    });
  });

  it("posts text for normalization without caching", async () => {
    const fetchMock = mockFetch(200, { normalized: "ދިވެހި" });

    await normalizeLanguageText("ދިވެހި");

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/nlp/normalize");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    expect(JSON.parse(String(init.body))).toEqual({ text: "ދިވެހި" });
  });

  it("sends an explicit transliteration direction", async () => {
    const fetchMock = mockFetch(200, { output: "ދިވެހި" });

    await transliterateLanguageText("dhivehi", "latin_to_thaana");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      text: "dhivehi",
      direction: "latin_to_thaana",
    });
  });

  it("submits a document for asynchronous semantic indexing", async () => {
    const fetchMock = mockFetch(202, { processing_state: "pending" });

    await createLanguageDocument({ title: "Weather", source: "manual", text: "މޫސުމް" });

    const call = fetchMock.mock.calls[0];
    expect(String(call?.[0])).toContain("/api/v1/nlp/documents");
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
      title: "Weather",
      source: "manual",
      text: "މޫސުމް",
    });
  });

  it("posts semantic search filters", async () => {
    const fetchMock = mockFetch(200, { hits: [] });

    await searchLanguageDocuments({ text: "weather", limit: 10, source: "news" });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      text: "weather",
      limit: 10,
      source: "news",
    });
  });
});

describe("proxy allowlist", () => {
  it("matches exactly the paths a reviewer needs", async () => {
    const { GET } = await import("../../app/api/v1/[...path]/route");
    const refused = await GET(new Request("http://localhost/api/v1/enrolments"), {
      params: Promise.resolve({ path: ["enrolments"] }),
    });
    expect(refused.status).toBe(404);
    expect((await refused.json()).error.code).toBe("not_proxied");
  });

  it("refuses a write to a path that is only readable", async () => {
    const { POST } = await import("../../app/api/v1/[...path]/route");
    const refused = await POST(new Request("http://localhost", { method: "POST" }), {
      params: Promise.resolve({
        path: ["identifications", "d484ac51-32bc-4da6-bf22-d94f23d45519", "image"],
      }),
    });
    expect(refused.status).toBe(404);
  });

  it("forwards authenticated system metrics as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ cpu_percent: 25 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("../../app/api/v1/[...path]/route");

    const response = await GET(new Request("http://localhost/api/v1/system/metrics"), {
      params: Promise.resolve({ path: ["system", "metrics"] }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ cpu_percent: 25 });
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Accept: "application/json",
      Authorization: "Bearer faceid_test-token",
    });
  });

  it("forwards authenticated live statistics as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ persons: 123 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("../../app/api/v1/[...path]/route");

    const response = await GET(new Request("http://localhost/api/v1/statistics"), {
      params: Promise.resolve({ path: ["statistics"] }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ persons: 123 });
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Accept: "application/json",
      Authorization: "Bearer faceid_test-token",
    });
  });

  it("forwards the media list with its bounded query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 8, offset: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("../../app/api/v1/[...path]/route");

    const response = await GET(new Request("http://localhost/api/v1/media?limit=8"), {
      params: Promise.resolve({ path: ["media"] }),
    });

    expect(response.status).toBe(200);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("http://api.test:8000/api/v1/media?limit=8");
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Accept: "application/json",
      Authorization: "Bearer faceid_test-token",
    });
  });

  it("rejects a cross-origin BlackGlass media submission", async () => {
    const { POST } = await import("../../app/api/v1/[...path]/route");
    const request = new Request("http://localhost/api/v1/media", {
      method: "POST",
      headers: { host: "localhost", origin: "https://attacker.example" },
      body: "not-media",
    });

    const response = await POST(request, { params: Promise.resolve({ path: ["media"] }) });

    expect(response.status).toBe(403);
    expect((await response.json()).error.code).toBe("origin_rejected");
  });

  it("forwards a same-origin BlackGlass media submission with operator authentication", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "stored" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("../../app/api/v1/[...path]/route");
    const form = new FormData();
    form.set("source_type", "blackglass");
    form.set("external_source_id", "bg-4471");
    form.set("file", new Blob(["small fixture"], { type: "text/plain" }), "fixture.txt");
    const request = new Request("http://localhost/api/v1/media", {
      method: "POST",
      headers: { host: "localhost", origin: "http://localhost" },
      body: form,
    });

    const response = await POST(request, { params: Promise.resolve({ path: ["media"] }) });

    expect(response.status).toBe(201);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("http://api.test:8000/api/v1/media");
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer faceid_test-token",
    });
  });
});

describe("authentication", () => {
  it("maps the demo username to the API account identifier", () => {
    expect(accountIdentifier(" Newbie ")).toBe("newbie@eagleeye.internal");
  });

  it("keeps existing email account identifiers usable", () => {
    expect(accountIdentifier("Admin@Admin.com")).toBe("admin@admin.com");
  });

  it("sends the reviewer's own token, not a shared credential", async () => {
    const fetchMock = mockFetch(200, { items: [], count: 0 });
    await fetchReviewQueue();
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer faceid_test-token",
    );
  });

  it("never sends a reviewer name in the body", async () => {
    const fetchMock = mockFetch(200, {});
    await submitReview("abc", { outcome: "confirmed", note: "same person" });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).not.toHaveProperty("reviewer");
  });
});
