/** Lightweight frame-difference helpers for browser camera motion gating. */

/**
 * Return the mean luminance change between two RGBA frames as a percentage.
 *
 * This is deliberately motion detection, not person detection. Its only job is
 * to avoid sending unchanged camera frames to the biometric API.
 */
export function motionPercent(previous: Uint8ClampedArray, current: Uint8ClampedArray): number {
  if (previous.length !== current.length || current.length % 4 !== 0) {
    throw new Error("motion frames must be equally sized RGBA arrays");
  }
  if (current.length === 0) return 0;

  let difference = 0;
  for (let index = 0; index < current.length; index += 4) {
    const previousLuma =
      previous[index]! * 0.2126 + previous[index + 1]! * 0.7152 + previous[index + 2]! * 0.0722;
    const currentLuma =
      current[index]! * 0.2126 + current[index + 1]! * 0.7152 + current[index + 2]! * 0.0722;
    difference += Math.abs(currentLuma - previousLuma);
  }

  const pixels = current.length / 4;
  return Math.min(100, (difference / pixels / 255) * 100);
}

export function shouldIdentifyFrame(fields: {
  motion: number;
  threshold: number;
  now: number;
  lastIdentificationAt: number;
  cooldownMs: number;
  identifying: boolean;
}): boolean {
  return (
    !fields.identifying &&
    fields.motion >= fields.threshold &&
    fields.now - fields.lastIdentificationAt >= fields.cooldownMs
  );
}
