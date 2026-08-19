import { FileClock } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Pagination } from "@/components/browse-controls";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge } from "@/components/states/status-badge";
import { ApiError, fetchAuditEvents, fetchIdentity } from "@/lib/api";
import { formatTime, shortId } from "@/lib/format";
import type { AuditPage as AuditPageData } from "@/lib/types";

export const dynamic = "force-dynamic";

const PAGE = 50;

export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<{ action?: string; actor?: string; offset?: string }>;
}) {
  const { action, actor, offset = "0" } = await searchParams;
  const start = Number.parseInt(offset, 10) || 0;

  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  if (!identity.scopes.includes("admin")) {
    return (
      <>
        <PageHeader title="Audit" />
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Reading the audit log needs the <span className="identifier">admin</span> scope.
        </p>
      </>
    );
  }

  let page: AuditPageData;
  try {
    page = await fetchAuditEvents({
      limit: PAGE,
      offset: start,
      action: action ?? null,
      actor: actor ?? null,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    return (
      <>
        <PageHeader title="Audit" />
        <ErrorState
          message={error instanceof ApiError ? error.message : "The API is unreachable."}
          code={error instanceof ApiError ? error.code : undefined}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Audit"
        description="Every administrative and review action, most recent first. The log is append-only: nothing can edit or remove an entry."
        actions={<StatusBadge tone="neutral">{page.total} events</StatusBadge>}
      />

      <div className="mb-3 flex flex-wrap gap-1.5">
        <Link
          href="/audit"
          className={`rounded-md border px-2.5 py-1.5 text-sm ${
            action === undefined
              ? "border-accent bg-accent/10 text-ink"
              : "border-line text-ink-muted hover:text-ink"
          }`}
        >
          All actions
        </Link>
        {page.actions.map((name) => (
          <Link
            key={name}
            href={`/audit?action=${encodeURIComponent(name)}`}
            className={`identifier rounded-md border px-2.5 py-1.5 ${
              action === name
                ? "border-accent bg-accent/10 text-ink"
                : "border-line hover:text-ink"
            }`}
          >
            {name}
          </Link>
        ))}
      </div>

      {page.items.length === 0 ? (
        <EmptyState
          icon={FileClock}
          title="No events match this filter"
          description="Actions are recorded as they happen; nothing is written retrospectively."
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Action</th>
                <th scope="col">Actor</th>
                <th scope="col">Subject</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((event) => (
                <tr key={event.audit_uuid} className="align-top">
                  <td className="whitespace-nowrap text-ink-muted">
                    {formatTime(event.occurred_at)}
                  </td>
                  <td className="identifier whitespace-nowrap">{event.action}</td>
                  <td>
                    <span className="block text-ink">{event.actor_identifier}</span>
                    {/* A machine's action must never read as a person's. */}
                    <StatusBadge tone={event.actor_kind === "system" ? "neutral" : "info"}>
                      {event.actor_kind}
                    </StatusBadge>
                  </td>
                  <td className="space-y-0.5">
                    {event.person_uuid ? (
                      <Link
                        href={`/persons/${event.person_uuid}`}
                        className="identifier block text-accent hover:underline"
                      >
                        person {shortId(event.person_uuid)}
                      </Link>
                    ) : null}
                    {event.identification_uuid ? (
                      <Link
                        href={`/matches/${event.identification_uuid}`}
                        className="identifier block text-accent hover:underline"
                      >
                        id {shortId(event.identification_uuid)}
                      </Link>
                    ) : null}
                    {event.policy_version ? (
                      <span className="identifier block">{event.policy_version}</span>
                    ) : null}
                  </td>
                  <td>
                    <Details details={event.details} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={page}
        basePath="/audit"
        extra={{ ...(action ? { action } : {}), ...(actor ? { actor } : {}) }}
      />
    </>
  );
}

/** The event's structured context, rendered as it was recorded. */
function Details({ details }: { details: Record<string, unknown> }) {
  const entries = Object.entries(details).filter(([, value]) => value !== null);
  if (entries.length === 0) return <span className="text-ink-faint">—</span>;

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-ink-faint">{key}</dt>
          <dd className="identifier break-all">
            {typeof value === "object" ? JSON.stringify(value) : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
