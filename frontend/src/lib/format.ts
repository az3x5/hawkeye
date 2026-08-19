/**
 * Presentation helpers.
 *
 * Deliberately literal about scores: a similarity is shown as the number it is.
 * Rendering it as a percentage would invite reading it as a probability, which
 * it is not.
 */

import type { DecisionOutcome, Thresholds } from "./types";

/** Render a cosine similarity. Never a percentage. */
export function formatScore(score: number | null): string {
  return score === null ? "—" : score.toFixed(4);
}

/** Render the gap to the runner-up. */
export function formatMargin(margin: number | null): string {
  if (margin === null) return "no runner-up";
  return margin.toFixed(4);
}

/** True when the top two candidates are close enough to deserve extra care. */
export function isNarrowMargin(margin: number | null): boolean {
  return margin !== null && margin < 0.05;
}

/** Render an ISO timestamp, or a dash. */
export function formatTime(iso: string | null): string {
  if (iso === null) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toISOString().replace("T", " ").slice(0, 19);
}

/**
 * How long something has been waiting, in words.
 *
 * A review queue is a backlog, so age is the thing a reviewer triages by — far
 * more useful at a glance than an absolute timestamp.
 */
export function formatAge(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "—";

  const seconds = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Shorten a uuid for display without losing its recognisability. */
export function shortId(uuid: string): string {
  return uuid.slice(0, 8);
}

/**
 * Where a score sits on the policy's scale, as a 0–1 fraction for positioning.
 *
 * Purely a coordinate for drawing: it is not a confidence, and the label beside
 * it always shows the raw similarity.
 */
export function scorePosition(score: number, floor = 0, ceiling = 1): number {
  if (ceiling <= floor) return 0;
  return Math.min(1, Math.max(0, (score - floor) / (ceiling - floor)));
}

/** The band a score falls in, for colouring. Mirrors the API's own rule. */
export function bandFor(score: number, thresholds: Thresholds): DecisionOutcome {
  if (score >= thresholds.accept_at) return "accept";
  if (score >= thresholds.review_at) return "review";
  return "reject";
}

/** Plain-language gloss of an outcome, for people who are not the API. */
export function describeOutcome(outcome: DecisionOutcome): string {
  switch (outcome) {
    case "accept":
      return "The system proposes this match";
    case "review":
      return "The system is not confident enough to decide";
    case "reject":
      return "The system proposes nobody";
  }
}
