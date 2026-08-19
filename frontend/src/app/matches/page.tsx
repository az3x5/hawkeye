import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Pagination } from "@/components/browse-controls";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import { ApiError, NotAuthenticatedError, fetchIdentificationHistory } from "@/lib/api";
import { formatScore, formatTime, shortId } from "@/lib/format";
import type { IdentificationRecord, Paged } from "@/lib/types";

export const dynamic = "force-dynamic";

const PAGE = 25;
const FILTERS = [
  { label: "All", params: {} },
  { label: "Accepted", params: { outcome: "accept" } },
  { label: "Sent to review", params: { outcome: "review" } },
  { label: "Rejected", params: { outcome: "reject" } },
  { label: "Reviewed", params: { reviewed: "true" } },
  { label: "Awaiting review", params: { outcome: "review", reviewed: "false" } },
];

export default async function MatchesPage({
  searchParams,
}: {
  searchParams: Promise<{ outcome?: string; reviewed?: string; offset?: string }>;
}) {
  const { outcome, reviewed, offset = "0" } = await searchParams;
  const start = Number.parseInt(offset, 10) || 0;

  let page: Paged<IdentificationRecord>;
  try {
    page = await fetchIdentificationHistory({
      limit: PAGE,
      offset: start,
      outcome: outcome ?? null,
      reviewed: reviewed === undefined ? null : reviewed === "true",
    });
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    return (
      <>
        <PageHeader title="Matches" />
        <ErrorState
          message={error instanceof ApiError ? error.message : "The API is unreachable."}
          code={error instanceof ApiError ? error.code : undefined}
        />
      </>
    );
  }

  const active = (params: Record<string, string>) =>
    (params.outcome ?? undefined) === outcome &&
    (params.reviewed ?? undefined) === reviewed;

  return (
    <>
      <PageHeader
        title="Matches"
        description="Every identification the system has made, with what a reviewer concluded and the policy in force at the time."
        actions={<StatusBadge tone="neutral">{page.total} recorded</StatusBadge>}
      />

      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((filter) => {
          const params = new URLSearchParams(filter.params as Record<string, string>);
          return (
            <Link
              key={filter.label}
              href={`/matches${params.toString() ? `?${params}` : ""}`}
              className={`rounded-md border px-2.5 py-1.5 text-sm ${
                active(filter.params as Record<string, string>)
                  ? "border-accent bg-accent/10 text-ink"
                  : "border-line text-ink-muted hover:text-ink"
              }`}
            >
              {filter.label}
            </Link>
          );
        })}
      </div>

      {page.items.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No identifications match this filter"
          description="Identifications appear here as soon as they are made, decided or not."
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col" className="w-16">Query</th>
                <th scope="col">Identification</th>
                <th scope="col">Proposed</th>
                <th scope="col">Person</th>
                <th scope="col" className="text-right">Score</th>
                <th scope="col">Review</th>
                <th scope="col">Policy</th>
                <th scope="col">When</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((record) => (
                <tr key={record.identification_uuid} className="hover:bg-surface-raised/50">
                  <td>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`/api/v1/identifications/${record.identification_uuid}/image`}
                      alt=""
                      aria-hidden="true"
                      className="size-11 rounded border border-line object-cover"
                    />
                  </td>
                  <td>
                    <Link
                      href={`/matches/${record.identification_uuid}`}
                      className="identifier text-accent hover:underline"
                    >
                      {shortId(record.identification_uuid)}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge tone={toneForOutcome(record.outcome)}>
                      {record.outcome}
                    </StatusBadge>
                  </td>
                  <td>
                    {record.best_person_uuid === null ? (
                      <span className="text-ink-faint">nobody</span>
                    ) : (
                      <Link
                        href={`/persons/${record.best_person_uuid}`}
                        className="identifier text-accent hover:underline"
                      >
                        {shortId(record.best_person_uuid)}
                      </Link>
                    )}
                  </td>
                  <td className="text-right">{formatScore(record.best_score)}</td>
                  <td>
                    {record.review_outcome === null ? (
                      <span className="text-ink-faint">
                        {record.outcome === "review" ? "awaiting" : "—"}
                      </span>
                    ) : (
                      <span className="flex flex-col gap-0.5">
                        <StatusBadge tone={toneForOutcome(record.review_outcome)}>
                          {record.review_outcome}
                        </StatusBadge>
                        <span className="text-xs text-ink-faint">{record.reviewed_by}</span>
                      </span>
                    )}
                  </td>
                  <td className="identifier">{record.policy_version}</td>
                  <td className="text-ink-muted">{formatTime(record.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={page}
        basePath="/matches"
        extra={{
          ...(outcome ? { outcome } : {}),
          ...(reviewed ? { reviewed } : {}),
        }}
      />
    </>
  );
}
