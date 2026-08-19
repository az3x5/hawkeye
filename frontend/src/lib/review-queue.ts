/**
 * Filtering and ordering for the review queue.
 *
 * The API returns proposals awaiting review, oldest first, and accepts only a
 * `limit`. There is no server-side filter or sort, so everything here operates
 * on the page that was loaded — which the UI states plainly, because a filter
 * that silently searches only part of the data is worse than no filter.
 */

import type { IdentificationSummary } from "./types";

export type SortKey = "oldest" | "newest" | "highest" | "lowest" | "closest";

export interface QueueFilters {
  /** Matches an identification id, person id, or policy version. */
  search: string;
  /** Only proposals whose top two candidates are nearly tied. */
  closeCallsOnly: boolean;
  /** Restrict to one policy version, or all of them. */
  policy: string | null;
  sort: SortKey;
}

export const DEFAULT_FILTERS: QueueFilters = {
  search: "",
  closeCallsOnly: false,
  policy: null,
  sort: "oldest",
};

/** Below this gap the top two candidates are close enough to deserve care. */
export const NARROW_MARGIN = 0.05;

export function isCloseCall(item: IdentificationSummary): boolean {
  return item.margin !== null && item.margin < NARROW_MARGIN;
}

/** Every policy version present in the loaded page, for the filter control. */
export function policiesIn(items: IdentificationSummary[]): string[] {
  return [...new Set(items.map((item) => item.policy_version))].sort();
}

function matchesSearch(item: IdentificationSummary, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (needle === "") return true;
  return (
    item.identification_uuid.toLowerCase().includes(needle) ||
    (item.best_person_uuid ?? "").toLowerCase().includes(needle) ||
    item.policy_version.toLowerCase().includes(needle)
  );
}

/** Ordering. Ties break on identification id so the order never wobbles. */
function compare(a: IdentificationSummary, b: IdentificationSummary, sort: SortKey): number {
  const byId = a.identification_uuid.localeCompare(b.identification_uuid);
  const score = (item: IdentificationSummary) => item.best_score ?? -Infinity;
  // A missing margin means no runner-up, which is the opposite of a close
  // call, so it sorts last rather than first.
  const margin = (item: IdentificationSummary) => item.margin ?? Infinity;

  switch (sort) {
    case "oldest":
      return a.created_at.localeCompare(b.created_at) || byId;
    case "newest":
      return b.created_at.localeCompare(a.created_at) || byId;
    case "highest":
      return score(b) - score(a) || byId;
    case "lowest":
      return score(a) - score(b) || byId;
    case "closest":
      return margin(a) - margin(b) || byId;
  }
}

/** Apply the filters and ordering to a loaded page of proposals. */
export function applyFilters(
  items: IdentificationSummary[],
  filters: QueueFilters,
): IdentificationSummary[] {
  return items
    .filter((item) => matchesSearch(item, filters.search))
    .filter((item) => !filters.closeCallsOnly || isCloseCall(item))
    .filter((item) => filters.policy === null || item.policy_version === filters.policy)
    .sort((a, b) => compare(a, b, filters.sort));
}

/**
 * Where an identification sits in the queue, and what follows it.
 *
 * Used for continuous triage: deciding one proposal should lead straight to
 * the next rather than back to a list the reviewer has to re-read.
 */
export interface QueuePosition {
  index: number;
  total: number;
  nextId: string | null;
  previousId: string | null;
}

export function locate(
  items: IdentificationSummary[],
  identificationUuid: string,
): QueuePosition | null {
  const index = items.findIndex((item) => item.identification_uuid === identificationUuid);
  if (index === -1) return null;
  return {
    index,
    total: items.length,
    nextId: items[index + 1]?.identification_uuid ?? null,
    previousId: items[index - 1]?.identification_uuid ?? null,
  };
}

/**
 * The item to move to once this one is decided.
 *
 * Deciding removes the current proposal from the queue, so the item that was
 * next becomes the one at this index; falling back to the previous one keeps
 * the reviewer working when they have just cleared the tail of the queue.
 */
export function afterDeciding(
  items: IdentificationSummary[],
  identificationUuid: string,
): string | null {
  const position = locate(items, identificationUuid);
  if (position === null) return null;
  return position.nextId ?? position.previousId;
}
