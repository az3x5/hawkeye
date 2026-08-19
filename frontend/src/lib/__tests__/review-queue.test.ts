import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  afterDeciding,
  applyFilters,
  isCloseCall,
  locate,
  policiesIn,
} from "../review-queue";
import type { IdentificationSummary } from "../types";

function item(over: Partial<IdentificationSummary> = {}): IdentificationSummary {
  return {
    identification_uuid: "aaaaaaaa-0000-0000-0000-000000000001",
    outcome: "review",
    created_at: "2026-08-19T10:00:00Z",
    best_person_uuid: "bbbbbbbb-0000-0000-0000-000000000001",
    best_score: 0.5,
    margin: 0.2,
    candidate_count: 2,
    policy_version: "local-dev-v1",
    ...over,
  };
}

describe("isCloseCall", () => {
  it("flags a near-tie between the top two candidates", () => {
    expect(isCloseCall(item({ margin: 0.01 }))).toBe(true);
    expect(isCloseCall(item({ margin: 0.2 }))).toBe(false);
  });

  it("does not treat a lone candidate as a close call", () => {
    expect(isCloseCall(item({ margin: null }))).toBe(false);
  });
});

describe("search", () => {
  const items = [
    item({ identification_uuid: "11111111-aaaa-0000-0000-000000000000" }),
    item({
      identification_uuid: "22222222-bbbb-0000-0000-000000000000",
      best_person_uuid: "cafe0000-0000-0000-0000-000000000000",
      policy_version: "strict-v2",
    }),
  ];

  it("matches an identification id", () => {
    const found = applyFilters(items, { ...DEFAULT_FILTERS, search: "22222222" });
    expect(found.map((i) => i.identification_uuid)).toEqual([
      "22222222-bbbb-0000-0000-000000000000",
    ]);
  });

  it("matches a person id and a policy version", () => {
    expect(applyFilters(items, { ...DEFAULT_FILTERS, search: "cafe" })).toHaveLength(1);
    expect(applyFilters(items, { ...DEFAULT_FILTERS, search: "strict" })).toHaveLength(1);
  });

  it("ignores case and surrounding space", () => {
    expect(applyFilters(items, { ...DEFAULT_FILTERS, search: "  STRICT-V2 " })).toHaveLength(1);
  });

  it("returns everything when the search is empty", () => {
    expect(applyFilters(items, DEFAULT_FILTERS)).toHaveLength(2);
  });

  it("returns nothing rather than everything when nothing matches", () => {
    expect(applyFilters(items, { ...DEFAULT_FILTERS, search: "nope" })).toHaveLength(0);
  });
});

describe("filters", () => {
  const items = [
    item({ identification_uuid: "1", margin: 0.01, policy_version: "a" }),
    item({ identification_uuid: "2", margin: 0.4, policy_version: "b" }),
  ];

  it("restricts to close calls", () => {
    const found = applyFilters(items, { ...DEFAULT_FILTERS, closeCallsOnly: true });
    expect(found.map((i) => i.identification_uuid)).toEqual(["1"]);
  });

  it("restricts to one policy", () => {
    const found = applyFilters(items, { ...DEFAULT_FILTERS, policy: "b" });
    expect(found.map((i) => i.identification_uuid)).toEqual(["2"]);
  });

  it("combines filters", () => {
    const found = applyFilters(items, {
      ...DEFAULT_FILTERS,
      closeCallsOnly: true,
      policy: "b",
    });
    expect(found).toHaveLength(0);
  });

  it("lists the policies present, sorted and deduplicated", () => {
    expect(policiesIn([...items, item({ policy_version: "a" })])).toEqual(["a", "b"]);
  });
});

describe("sorting", () => {
  const items = [
    item({ identification_uuid: "old", created_at: "2026-08-01T00:00:00Z", best_score: 0.9, margin: 0.3 }),
    item({ identification_uuid: "new", created_at: "2026-08-19T00:00:00Z", best_score: 0.4, margin: 0.01 }),
  ];

  it("defaults to the longest wait first", () => {
    expect(applyFilters(items, DEFAULT_FILTERS)[0]?.identification_uuid).toBe("old");
  });

  it("can put the newest first", () => {
    expect(
      applyFilters(items, { ...DEFAULT_FILTERS, sort: "newest" })[0]?.identification_uuid,
    ).toBe("new");
  });

  it("can order by similarity in both directions", () => {
    expect(
      applyFilters(items, { ...DEFAULT_FILTERS, sort: "highest" })[0]?.identification_uuid,
    ).toBe("old");
    expect(
      applyFilters(items, { ...DEFAULT_FILTERS, sort: "lowest" })[0]?.identification_uuid,
    ).toBe("new");
  });

  it("can bring the closest calls to the top", () => {
    expect(
      applyFilters(items, { ...DEFAULT_FILTERS, sort: "closest" })[0]?.identification_uuid,
    ).toBe("new");
  });

  it("sorts a missing margin last, not first", () => {
    const withNone = [...items, item({ identification_uuid: "lone", margin: null })];
    const order = applyFilters(withNone, { ...DEFAULT_FILTERS, sort: "closest" }).map(
      (i) => i.identification_uuid,
    );
    expect(order.at(-1)).toBe("lone");
  });

  it("sorts a missing score last when ordering by highest", () => {
    const withNone = [...items, item({ identification_uuid: "none", best_score: null })];
    const order = applyFilters(withNone, { ...DEFAULT_FILTERS, sort: "highest" }).map(
      (i) => i.identification_uuid,
    );
    expect(order.at(-1)).toBe("none");
  });

  it("is stable for ties, so the order does not wobble", () => {
    const tied = [
      item({ identification_uuid: "b", created_at: "2026-08-01T00:00:00Z" }),
      item({ identification_uuid: "a", created_at: "2026-08-01T00:00:00Z" }),
    ];
    expect(applyFilters(tied, DEFAULT_FILTERS).map((i) => i.identification_uuid)).toEqual([
      "a",
      "b",
    ]);
  });

  it("does not mutate the caller's array", () => {
    const original = [...items];
    applyFilters(items, { ...DEFAULT_FILTERS, sort: "newest" });
    expect(items).toEqual(original);
  });
});

describe("position in the queue", () => {
  const items = [item({ identification_uuid: "1" }), item({ identification_uuid: "2" }), item({ identification_uuid: "3" })];

  it("reports where an item sits and what surrounds it", () => {
    expect(locate(items, "2")).toEqual({
      index: 1,
      total: 3,
      nextId: "3",
      previousId: "1",
    });
  });

  it("has no neighbour beyond the ends", () => {
    expect(locate(items, "1")?.previousId).toBeNull();
    expect(locate(items, "3")?.nextId).toBeNull();
  });

  it("returns null for an item that is not in the queue", () => {
    expect(locate(items, "absent")).toBeNull();
  });
});

describe("afterDeciding", () => {
  const items = [item({ identification_uuid: "1" }), item({ identification_uuid: "2" })];

  it("moves to the next proposal", () => {
    expect(afterDeciding(items, "1")).toBe("2");
  });

  it("falls back to the previous one at the tail of the queue", () => {
    expect(afterDeciding(items, "2")).toBe("1");
  });

  it("has nowhere to go when it was the only one", () => {
    expect(afterDeciding([item({ identification_uuid: "only" })], "only")).toBeNull();
  });

  it("has nowhere to go for an unknown item", () => {
    expect(afterDeciding(items, "absent")).toBeNull();
  });
});
