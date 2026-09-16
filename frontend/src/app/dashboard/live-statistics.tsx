"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import type { Statistics } from "@/lib/types";

const REFRESH_MS = 5_000;

export function LiveStatistics({
  initial,
  initialProblem,
}: {
  initial: Statistics | null;
  initialProblem: string | null;
}) {
  const router = useRouter();
  const [stats, setStats] = useState(initial);
  const [problem, setProblem] = useState(initialProblem);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const controller = new AbortController();

    async function refresh() {
      try {
        const response = await fetch("/api/v1/statistics", {
          headers: { Accept: "application/json" },
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) {
          router.replace("/sign-in");
          return;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = (await response.json()) as Statistics;
        if (active) {
          setStats(next);
          setProblem(null);
          setStale(false);
        }
      } catch (error) {
        if (active && !(error instanceof DOMException && error.name === "AbortError")) {
          setProblem("Counts could not be read from the API.");
          setStale(true);
        }
      } finally {
        if (active) timer = window.setTimeout(refresh, REFRESH_MS);
      }
    }

    void refresh();
    return () => {
      active = false;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [router]);

  if (stats === null) {
    return <p className="panel px-4 py-6 text-sm text-ink-muted">{problem}</p>;
  }

  return (
    <section className="space-y-4" aria-label="Live system counts">
      <div className="flex items-center justify-end gap-2 text-xs text-ink-faint" aria-live="polite">
        <span className={`size-1.5 rounded-full ${stale ? "bg-review" : "bg-accept"}`} aria-hidden="true" />
        <span className={stale ? "text-review" : undefined}>
          {stale ? "Update delayed" : "Live · refreshes every 5 seconds"}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="People" value={stats.persons} href="/persons" />
        <Metric label="BlackGlass records" value={stats.language_documents} href="/evidence" />
        <Metric label="Face samples" value={stats.face_samples} />
        <Metric label="Embeddings stored" value={stats.embeddings} />
        <Metric
          label="Awaiting review"
          value={stats.awaiting_review}
          href="/review"
          tone={stats.awaiting_review > 0 ? "review" : undefined}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Breakdown
          title="BlackGlass records by state"
          counts={stats.language_documents_by_state}
          total={stats.language_documents}
          empty="No BlackGlass text records have been imported yet."
          href="/evidence"
        />
        <Breakdown
          title="Face samples by state"
          counts={stats.samples_by_state}
          total={stats.face_samples}
          empty="No faces have been enrolled yet."
        />
        <Breakdown
          title="Identifications by outcome"
          counts={stats.identifications_by_outcome}
          total={stats.identifications}
          empty="No identifications have been made yet."
          href="/matches"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="Identifications" value={stats.identifications} href="/matches" />
        <Metric label="Reviews recorded" value={stats.reviews_recorded} />
        <Metric label="Audit events" value={stats.audit_events} href="/audit" />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  href,
  tone,
}: {
  label: string;
  value: number;
  href?: string;
  tone?: "review";
}) {
  const body = (
    <div className="panel p-4">
      <p className="text-xs text-ink-faint">{label}</p>
      <p
        className={`mt-1 text-2xl font-semibold tabular-nums ${
          tone === "review" && value > 0 ? "text-review" : "text-ink"
        }`}
      >
        {value.toLocaleString()}
      </p>
    </div>
  );
  return href ? (
    <Link href={href} className="block transition-colors hover:brightness-110">
      {body}
    </Link>
  ) : (
    body
  );
}

function Breakdown({
  title,
  counts,
  total,
  empty,
  href,
}: {
  title: string;
  counts: Record<string, number>;
  total: number;
  empty: string;
  href?: string;
}) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  return (
    <section className="panel">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {href ? (
          <Link href={href} className="text-xs text-accent hover:underline">
            View all
          </Link>
        ) : null}
      </div>
      {entries.length === 0 ? (
        <p className="px-4 py-6 text-sm text-ink-muted">{empty}</p>
      ) : (
        <ul className="divide-y divide-line">
          {entries.map(([name, count]) => (
            <li key={name} className="flex items-center gap-3 px-4 py-2.5">
              <StatusBadge tone={toneForOutcome(name)}>{name}</StatusBadge>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-sunken">
                <div
                  className="h-full rounded-full bg-ink-faint transition-[width] duration-500"
                  style={{ width: `${total === 0 ? 0 : (count / total) * 100}%` }}
                />
              </div>
              <span className="text-sm tabular-nums text-ink">{count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
