import { describe, expect, it } from "vitest";
import { intersectionOverUnion, reconcileTracks, type FaceTrack } from "@/lib/tracking";

const detection = (x: number, personUuid: string | null = null) => ({
  box: { x1: x, y1: 10, x2: x + 100, y2: 110 },
  personUuid,
  outcome: personUuid ? ("accept" as const) : ("reject" as const),
  score: personUuid ? 0.8 : null,
});

const track = (trackId: string, x: number, personUuid: string | null = null): FaceTrack => ({
  ...detection(x, personUuid),
  trackId,
  firstSeenAt: 100,
  lastSeenAt: 100,
  sightingCount: 1,
  missedAnalyses: 0,
});

describe("intersectionOverUnion", () => {
  it("returns one for identical boxes", () => {
    expect(intersectionOverUnion(detection(0).box, detection(0).box)).toBe(1);
  });

  it("returns zero for disjoint boxes", () => {
    expect(intersectionOverUnion(detection(0).box, detection(200).box)).toBe(0);
  });
});

describe("reconcileTracks", () => {
  it("keeps a stable id when a face moves slightly", () => {
    const result = reconcileTracks([track("T001", 10)], [detection(18)], 200, 2);
    expect(result.visible[0]?.trackId).toBe("T001");
    expect(result.visible[0]?.sightingCount).toBe(2);
    expect(result.nextTrackNumber).toBe(2);
  });

  it("creates deterministic ids for new faces", () => {
    const result = reconcileTracks([], [detection(10), detection(200)], 200, 7);
    expect(result.visible.map((item) => item.trackId)).toEqual(["T007", "T008"]);
    expect(result.nextTrackNumber).toBe(9);
  });

  it("does not merge different known people", () => {
    const result = reconcileTracks(
      [track("T001", 10, "person-a")],
      [detection(12, "person-b")],
      200,
      2,
    );
    expect(result.visible[0]?.trackId).toBe("T002");
  });

  it("retires tracks after the missed-analysis allowance", () => {
    const old = { ...track("T001", 10), missedAnalyses: 3 };
    expect(reconcileTracks([old], [], 200, 2).active).toEqual([]);
  });
});
