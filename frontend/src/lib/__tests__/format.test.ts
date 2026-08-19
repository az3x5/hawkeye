import { describe, expect, it } from "vitest";
import {
  bandFor,
  describeOutcome,
  formatAge,
  formatMargin,
  formatScore,
  formatTime,
  isNarrowMargin,
  scorePosition,
  shortId,
} from "../format";

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

describe("formatAge", () => {
  const now = new Date("2026-08-19T12:00:00Z");

  it("says just now for something seconds old", () => {
    expect(formatAge("2026-08-19T11:59:30Z", now)).toBe("just now");
  });

  it("counts minutes, hours and days", () => {
    expect(formatAge("2026-08-19T11:30:00Z", now)).toBe("30 min ago");
    expect(formatAge("2026-08-19T09:00:00Z", now)).toBe("3 hrs ago");
    expect(formatAge("2026-08-17T12:00:00Z", now)).toBe("2 days ago");
  });

  it("uses the singular where it should", () => {
    expect(formatAge("2026-08-19T11:00:00Z", now)).toBe("1 hr ago");
    expect(formatAge("2026-08-18T12:00:00Z", now)).toBe("1 day ago");
  });

  it("tolerates an unparseable timestamp", () => {
    expect(formatAge("not a date", now)).toBe("—");
  });
});

describe("scorePosition", () => {
  it("maps a score onto a 0-1 coordinate", () => {
    expect(scorePosition(0.5)).toBeCloseTo(0.5);
    expect(scorePosition(0)).toBe(0);
    expect(scorePosition(1)).toBe(1);
  });

  it("clamps scores outside the drawn range", () => {
    expect(scorePosition(-0.4)).toBe(0);
    expect(scorePosition(1.5)).toBe(1);
  });

  it("refuses to divide by an empty range", () => {
    expect(scorePosition(0.5, 1, 1)).toBe(0);
  });
});

describe("bandFor", () => {
  const thresholds = { accept_at: 0.62, review_at: 0.42, policy_version: "v1" };

  it("mirrors the API's banding, boundaries included", () => {
    expect(bandFor(0.9, thresholds)).toBe("accept");
    expect(bandFor(0.62, thresholds)).toBe("accept");
    expect(bandFor(0.5, thresholds)).toBe("review");
    expect(bandFor(0.42, thresholds)).toBe("review");
    expect(bandFor(0.41, thresholds)).toBe("reject");
  });
});

describe("describeOutcome", () => {
  it("never claims certainty the system does not have", () => {
    expect(describeOutcome("accept")).toContain("proposes");
    expect(describeOutcome("review")).toContain("not confident enough");
    expect(describeOutcome("reject")).toContain("nobody");
  });
});
