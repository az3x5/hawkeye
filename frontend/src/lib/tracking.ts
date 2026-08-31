import type { DecisionOutcome, LiveFaceBox } from "@/lib/types";

export interface TrackDetection {
  box: LiveFaceBox;
  personUuid: string | null;
  outcome: DecisionOutcome;
  score: number | null;
}

export interface FaceTrack extends TrackDetection {
  trackId: string;
  firstSeenAt: number;
  lastSeenAt: number;
  sightingCount: number;
  missedAnalyses: number;
}

export interface ReconciledTracks {
  active: FaceTrack[];
  visible: FaceTrack[];
  nextTrackNumber: number;
}

interface CandidatePair {
  trackIndex: number;
  detectionIndex: number;
  overlap: number;
}

/**
 * Associate new face boxes with recent tracks using deterministic greedy IoU.
 *
 * Known identities are never joined across different people even when their
 * boxes overlap. Tracks survive a few missed analyses to tolerate detector
 * flicker, but only tracks seen in the current analysis are returned as
 * `visible` for overlay rendering.
 */
export function reconcileTracks(
  previous: FaceTrack[],
  detections: TrackDetection[],
  now: number,
  nextTrackNumber: number,
  minimumOverlap = 0.2,
  maximumMissedAnalyses = 3,
): ReconciledTracks {
  const candidates: CandidatePair[] = [];
  previous.forEach((track, trackIndex) => {
    detections.forEach((detection, detectionIndex) => {
      if (
        track.personUuid !== null &&
        detection.personUuid !== null &&
        track.personUuid !== detection.personUuid
      ) {
        return;
      }
      const overlap = intersectionOverUnion(track.box, detection.box);
      if (overlap >= minimumOverlap) candidates.push({ trackIndex, detectionIndex, overlap });
    });
  });
  candidates.sort(
    (left, right) =>
      right.overlap - left.overlap ||
      left.trackIndex - right.trackIndex ||
      left.detectionIndex - right.detectionIndex,
  );

  const usedTracks = new Set<number>();
  const usedDetections = new Set<number>();
  const visible: FaceTrack[] = [];
  for (const candidate of candidates) {
    if (usedTracks.has(candidate.trackIndex) || usedDetections.has(candidate.detectionIndex)) continue;
    usedTracks.add(candidate.trackIndex);
    usedDetections.add(candidate.detectionIndex);
    const track = previous[candidate.trackIndex]!;
    const detection = detections[candidate.detectionIndex]!;
    visible.push({
      ...detection,
      trackId: track.trackId,
      firstSeenAt: track.firstSeenAt,
      lastSeenAt: now,
      sightingCount: track.sightingCount + 1,
      missedAnalyses: 0,
    });
  }

  for (const [detectionIndex, detection] of detections.entries()) {
    if (usedDetections.has(detectionIndex)) continue;
    visible.push({
      ...detection,
      trackId: `T${String(nextTrackNumber).padStart(3, "0")}`,
      firstSeenAt: now,
      lastSeenAt: now,
      sightingCount: 1,
      missedAnalyses: 0,
    });
    nextTrackNumber += 1;
  }

  const retained = previous
    .filter((_track, index) => !usedTracks.has(index))
    .map((track) => ({ ...track, missedAnalyses: track.missedAnalyses + 1 }))
    .filter((track) => track.missedAnalyses <= maximumMissedAnalyses);

  return {
    active: [...visible, ...retained],
    visible: visible.sort((left, right) => left.trackId.localeCompare(right.trackId)),
    nextTrackNumber,
  };
}

export function intersectionOverUnion(left: LiveFaceBox, right: LiveFaceBox): number {
  const width = Math.max(0, Math.min(left.x2, right.x2) - Math.max(left.x1, right.x1));
  const height = Math.max(0, Math.min(left.y2, right.y2) - Math.max(left.y1, right.y1));
  const intersection = width * height;
  if (intersection === 0) return 0;
  const leftArea = Math.max(0, left.x2 - left.x1) * Math.max(0, left.y2 - left.y1);
  const rightArea = Math.max(0, right.x2 - right.x1) * Math.max(0, right.y2 - right.y1);
  const union = leftArea + rightArea - intersection;
  return union <= 0 ? 0 : intersection / union;
}
