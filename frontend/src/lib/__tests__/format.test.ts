import { describe, expect, it } from "vitest";
import { formatMargin, formatScore, formatTime, isNarrowMargin, shortId } from "../format";

describe("formatScore", () => {
  it("renders a similarity as a plain number", () => {
    expect(formatScore(0.8123456)).toBe("0.8123");
  });

  it("never renders a percentage", () => {
    expect(formatScore(0.5)).not.toContain("%");
  });

  it("keeps negative similarities visible", () => {
    expect(formatScore(-0.42)).toBe("-0.4200");
  });

  it("shows a dash when there is no score", () => {
    expect(formatScore(null)).toBe("—");
  });
});

describe("margin", () => {
  it("reports the gap to the runner-up", () => {
    expect(formatMargin(0.0612)).toBe("0.0612");
  });

  it("says so when there is no runner-up", () => {
    expect(formatMargin(null)).toBe("no runner-up");
    expect(isNarrowMargin(null)).toBe(false);
  });

  it("flags a narrow gap for extra care", () => {
    expect(isNarrowMargin(0.01)).toBe(true);
    expect(isNarrowMargin(0.2)).toBe(false);
  });
});

describe("formatTime", () => {
  it("renders an ISO timestamp readably", () => {
    expect(formatTime("2026-08-19T10:20:30Z")).toBe("2026-08-19 10:20:30");
  });

  it("tolerates a missing or unparseable value", () => {
    expect(formatTime(null)).toBe("—");
    expect(formatTime("not a date")).toBe("—");
  });
});

describe("shortId", () => {
  it("shortens a uuid without losing recognisability", () => {
    expect(shortId("7dfc313a-1111-2222-3333-444455556666")).toBe("7dfc313a");
  });
});
