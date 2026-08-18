/**
 * Presentation helpers.
 *
 * Deliberately small and deliberately literal: a similarity is shown as the
 * number it is. Rendering it as a percentage would invite reading it as a
 * probability, which it is not.
 */

/** Render a cosine similarity. Never a percentage. */
export function formatScore(score: number | null): string {
  return score === null ? "—" : score.toFixed(4);
}

/** Render the gap to the runner-up, marking a narrow one as worth noticing. */
export function formatMargin(margin: number | null): string {
  if (margin === null) return "no runner-up";
  return margin.toFixed(4);
}

/** True when the top two candidates are close enough to deserve extra care. */
export function isNarrowMargin(margin: number | null): boolean {
  return margin !== null && margin < 0.05;
}

/** Render an ISO timestamp in the reviewer's locale, or a dash. */
export function formatTime(iso: string | null): string {
  if (iso === null) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toISOString().replace("T", " ").slice(0, 19);
}

/** Shorten a uuid for display without losing its recognisability. */
export function shortId(uuid: string): string {
  return uuid.slice(0, 8);
}
