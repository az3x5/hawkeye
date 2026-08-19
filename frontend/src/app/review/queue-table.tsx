"use client";

import { ClipboardCheck, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "@/components/states/empty-state";
import { StatusBadge } from "@/components/states/status-badge";
import { formatAge, formatScore, shortId } from "@/lib/format";
import {
  DEFAULT_FILTERS,
  applyFilters,
  isCloseCall,
  policiesIn,
  type QueueFilters,
  type SortKey,
} from "@/lib/review-queue";
import type { IdentificationSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const SORTS: { value: SortKey; label: string }[] = [
  { value: "oldest", label: "Waiting longest" },
  { value: "newest", label: "Most recent" },
  { value: "highest", label: "Highest similarity" },
  { value: "lowest", label: "Lowest similarity" },
  { value: "closest", label: "Closest calls" },
];

const CONTROL =
  "rounded-md border border-line bg-surface-sunken px-2.5 py-1.5 text-sm text-ink";

/**
 * The review backlog.
 *
 * Filtering and ordering happen over the page the API returned, because the
 * endpoint accepts only a limit. The count line says so rather than implying a
 * search across everything ever recorded.
 */
export function QueueTable({ items }: { items: IdentificationSummary[] }) {
  const router = useRouter();
  const [filters, setFilters] = useState<QueueFilters>(DEFAULT_FILTERS);
  const [cursor, setCursor] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);

  const visible = useMemo(() => applyFilters(items, filters), [items, filters]);
  const policies = useMemo(() => policiesIn(items), [items]);

  // Derived rather than stored: filters narrow the list constantly, and
  // clamping in an effect would re-render on every change.
  const active = Math.min(cursor, Math.max(visible.length - 1, 0));

  // Triage keys, so a reviewer can work the queue without the mouse.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);

      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setCursor((c) => Math.min(c + 1, visible.length - 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
      } else if (event.key === "Enter") {
        const selected = visible[active];
        if (selected) router.push(`/review/${selected.identification_uuid}`);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, active, router]);

  const filtered = visible.length !== items.length;

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-ink-faint"
            aria-hidden="true"
          />
          <input
            ref={searchRef}
            type="search"
            value={filters.search}
            onChange={(event) => setFilters({ ...filters, search: event.target.value })}
            placeholder="Filter by id or policy…"
            aria-label="Filter the loaded queue"
            className={cn(CONTROL, "w-56 pl-8")}
          />
        </div>

        <select
          value={filters.sort}
          onChange={(event) =>
            setFilters({ ...filters, sort: event.target.value as SortKey })
          }
          aria-label="Order by"
          className={CONTROL}
        >
          {SORTS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        {policies.length > 1 ? (
          <select
            value={filters.policy ?? ""}
            onChange={(event) =>
              setFilters({ ...filters, policy: event.target.value || null })
            }
            aria-label="Policy version"
            className={CONTROL}
          >
            <option value="">All policies</option>
            {policies.map((policy) => (
              <option key={policy} value={policy}>
                {policy}
              </option>
            ))}
          </select>
        ) : null}

        <label className="flex items-center gap-2 text-sm text-ink-muted">
          <input
            type="checkbox"
            checked={filters.closeCallsOnly}
            onChange={(event) =>
              setFilters({ ...filters, closeCallsOnly: event.target.checked })
            }
            className="size-4 rounded border-line bg-surface-sunken"
          />
          Close calls only
        </label>

        <p className="ml-auto text-xs text-ink-faint">
          {filtered
            ? `${visible.length} of ${items.length} loaded`
            : `${items.length} loaded`}
          <span className="mx-1.5">·</span>
          <kbd className="rounded bg-surface-raised px-1">j</kbd>{" "}
          <kbd className="rounded bg-surface-raised px-1">k</kbd> move,{" "}
          <kbd className="rounded bg-surface-raised px-1">↵</kbd> open,{" "}
          <kbd className="rounded bg-surface-raised px-1">/</kbd> filter
        </p>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="Nothing matches these filters"
          description="Filters apply to the proposals loaded from the API, not to every identification ever recorded."
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col" className="w-16">
                  Query
                </th>
                <th scope="col">Identification</th>
                <th scope="col">Waiting</th>
                <th scope="col" className="text-right">
                  Top similarity
                </th>
                <th scope="col" className="text-right">
                  Margin
                </th>
                <th scope="col" className="text-right">
                  Candidates
                </th>
                <th scope="col">Policy</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item, index) => (
                <tr
                  key={item.identification_uuid}
                  onClick={() => router.push(`/review/${item.identification_uuid}`)}
                  onMouseEnter={() => setCursor(index)}
                  aria-selected={index === active}
                  className={cn(
                    "cursor-pointer",
                    index === active ? "bg-surface-raised" : "hover:bg-surface-raised/50",
                  )}
                >
                  <td>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`/api/v1/identifications/${item.identification_uuid}/image`}
                      alt=""
                      aria-hidden="true"
                      className="size-11 rounded border border-line object-cover"
                    />
                  </td>
                  <td className="identifier text-accent">
                    {shortId(item.identification_uuid)}
                  </td>
                  <td className="text-ink-muted">{formatAge(item.created_at)}</td>
                  <td className="text-right font-semibold">
                    {formatScore(item.best_score)}
                  </td>
                  <td className="text-right">
                    {isCloseCall(item) ? (
                      <StatusBadge tone="review">close call</StatusBadge>
                    ) : (
                      <span className="text-ink-muted">{formatScore(item.margin)}</span>
                    )}
                  </td>
                  <td className="text-right text-ink-muted">{item.candidate_count}</td>
                  <td className="identifier">{item.policy_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
