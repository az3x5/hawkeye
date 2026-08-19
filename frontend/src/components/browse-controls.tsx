"use client";

import { Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Paged } from "@/lib/types";

const CONTROL =
  "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink";

/**
 * Search that survives a reload.
 *
 * The term lives in the URL rather than in component state, so a filtered view
 * can be shared, bookmarked and paged through without losing itself.
 */
export function SearchBox({
  placeholder,
  defaultValue,
  basePath,
}: {
  placeholder: string;
  defaultValue: string;
  basePath: string;
}) {
  const router = useRouter();
  const [value, setValue] = useState(defaultValue);

  function apply() {
    const params = new URLSearchParams();
    if (value.trim() !== "") params.set("search", value.trim());
    router.push(`${basePath}${params.toString() ? `?${params}` : ""}`);
  }

  return (
    <div className="mb-3 flex max-w-lg gap-2">
      <div className="relative flex-1">
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-ink-faint"
          aria-hidden="true"
        />
        <input
          type="search"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") apply();
          }}
          placeholder={placeholder}
          aria-label={placeholder}
          className={`${CONTROL} pl-8`}
        />
      </div>
      <button
        type="button"
        onClick={apply}
        className="shrink-0 rounded-md border border-line px-3 py-2 text-sm text-ink-muted hover:text-ink"
      >
        Search
      </button>
    </div>
  );
}

/** Paging that states the range rather than only offering arrows. */
export function Pagination<T>({
  page,
  basePath,
  extra = {},
}: {
  page: Paged<T>;
  basePath: string;
  extra?: Record<string, string>;
}) {
  if (page.total <= page.limit) return null;

  const from = page.offset + 1;
  const to = Math.min(page.offset + page.limit, page.total);

  function href(offset: number): string {
    const params = new URLSearchParams(extra);
    if (offset > 0) params.set("offset", String(offset));
    return `${basePath}${params.toString() ? `?${params}` : ""}`;
  }

  const previous = Math.max(0, page.offset - page.limit);
  const next = page.offset + page.limit;

  return (
    <nav className="mt-3 flex items-center justify-between text-sm" aria-label="Pagination">
      <p className="text-ink-faint tabular-nums">
        {from}–{to} of {page.total}
      </p>
      <div className="flex gap-2">
        {page.offset > 0 ? (
          <Link
            href={href(previous)}
            className="rounded-md border border-line px-3 py-1.5 text-ink-muted hover:text-ink"
          >
            Previous
          </Link>
        ) : null}
        {next < page.total ? (
          <Link
            href={href(next)}
            className="rounded-md border border-line px-3 py-1.5 text-ink-muted hover:text-ink"
          >
            Next
          </Link>
        ) : null}
      </div>
    </nav>
  );
}
