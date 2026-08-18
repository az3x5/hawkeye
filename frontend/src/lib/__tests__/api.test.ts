import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchIdentification, fetchReviewQueue, submitReview } from "../api";

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
    await expect(submitReview("abc", { outcome: "confirmed", reviewer: "alice" })).rejects.toThrow(
      ApiError,
    );
    try {
      await submitReview("abc", { outcome: "confirmed", reviewer: "alice" });
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
    await submitReview("abc", { outcome: "rejected", reviewer: "bob", note: "not the same" });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      outcome: "rejected",
      reviewer: "bob",
      note: "not the same",
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
});
