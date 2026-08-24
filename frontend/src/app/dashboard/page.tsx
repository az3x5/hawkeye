import { redirect } from "next/navigation";
import Link from "next/link";
import { PageHeader } from "@/components/shell/page-header";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import { SystemStatus } from "@/components/system-status";
import { HardwareUsage } from "@/components/hardware-usage";
import { ApiError, NotAuthenticatedError, fetchStatistics, fetchSystemMetrics } from "@/lib/api";
import type { Statistics, SystemMetrics } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let stats: Statistics | null = null;
  let systemMetrics: SystemMetrics | null = null;
  let problem: string | null = null;

  try {
    stats = await fetchStatistics();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    problem =
      error instanceof ApiError ? error.message : "Counts could not be read from the API.";
  }

  try {
    systemMetrics = await fetchSystemMetrics();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="What the system currently holds. Counts only — the system keeps no time series, so there are no rates or trends to show."
      />

      <div className="space-y-4">
        {stats === null ? (
          <p className="panel px-4 py-6 text-sm text-ink-muted">{problem}</p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Metric label="People" value={stats.persons} href="/persons" />
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
          </>
        )}

        <HardwareUsage initial={systemMetrics} />
        <SystemStatus />
      </div>
    </>
  );
}

/** One count. Nothing is derived, averaged or projected. */
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

/** A count split by category, with the proportion drawn to scale. */
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
                  className="h-full rounded-full bg-ink-faint"
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
