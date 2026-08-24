import { describe, expect, it } from "vitest";
import { motionPercent, shouldIdentifyFrame } from "@/lib/motion";

describe("motionPercent", () => {
  it("reports no change for identical frames", () => {
    const frame = new Uint8ClampedArray([10, 20, 30, 255, 40, 50, 60, 255]);
    expect(motionPercent(frame, frame)).toBe(0);
  });

  it("reports full change from black to white", () => {
    const black = new Uint8ClampedArray([0, 0, 0, 255]);
    const white = new Uint8ClampedArray([255, 255, 255, 255]);
    expect(motionPercent(black, white)).toBeCloseTo(100);
  });

  it("rejects frames with incompatible shapes", () => {
    expect(() => motionPercent(new Uint8ClampedArray(4), new Uint8ClampedArray(8))).toThrow(
      "equally sized",
    );
  });
});

describe("shouldIdentifyFrame", () => {
  const ready = {
    motion: 8,
    threshold: 5,
    now: 10_000,
    lastIdentificationAt: 5_000,
    cooldownMs: 3_000,
    identifying: false,
  };

  it("admits motion after the cooldown", () => {
    expect(shouldIdentifyFrame(ready)).toBe(true);
  });

  it("refuses unchanged, busy, and cooling-down frames", () => {
    expect(shouldIdentifyFrame({ ...ready, motion: 2 })).toBe(false);
    expect(shouldIdentifyFrame({ ...ready, identifying: true })).toBe(false);
    expect(shouldIdentifyFrame({ ...ready, lastIdentificationAt: 9_000 })).toBe(false);
  });
});
