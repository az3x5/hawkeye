import { describe, expect, it } from "vitest";
import { withLocalOffset } from "@/app/enrollments/enrol-console";

describe("withLocalOffset", () => {
  it("attaches an offset rather than assuming UTC", () => {
    const rendered = withLocalOffset("2026-08-19T14:30");
    expect(rendered).toMatch(/^2026-08-19T14:30:00[+-]\d{2}:\d{2}$/);
  });

  it("keeps the wall-clock time the operator typed", () => {
    expect(withLocalOffset("2026-08-19T14:30")).toContain("2026-08-19T14:30:00");
  });

  it("describes the same instant the browser means", () => {
    const rendered = withLocalOffset("2026-08-19T14:30");
    expect(new Date(rendered).getTime()).toBe(new Date("2026-08-19T14:30").getTime());
  });

  it("leaves an unparseable value alone rather than inventing an offset", () => {
    expect(withLocalOffset("not a date")).toBe("not a date");
  });
});
