import { Users } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge } from "@/components/states/status-badge";
import { ApiError, NotAuthenticatedError, fetchPersons } from "@/lib/api";
import { formatTime, shortId } from "@/lib/format";
import type { Paged, PersonSummary } from "@/lib/types";
import { Pagination, SearchBox } from "@/components/browse-controls";

export const dynamic = "force-dynamic";

const PAGE = 25;

export default async function PersonsPage({
  searchParams,
}: {
  searchParams: Promise<{ search?: string; offset?: string }>;
}) {
  const { search = "", offset = "0" } = await searchParams;
  const start = Number.parseInt(offset, 10) || 0;

  let page: Paged<PersonSummary>;
  try {
    page = await fetchPersons({ limit: PAGE, offset: start, search: search || null });
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    return (
      <>
        <PageHeader title="Persons" />
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
        title="Persons"
        description="People known to the system. Search matches a person's uuid or any external identifier."
        actions={<StatusBadge tone="neutral">{page.total} known</StatusBadge>}
      />

      <SearchBox
        placeholder="Search by uuid or external identifier…"
        defaultValue={search}
        basePath="/persons"
      />

      {page.items.length === 0 ? (
        <EmptyState
          icon={Users}
          title={search ? "Nobody matches that search" : "Nobody is enrolled yet"}
          description={
            search
              ? "Search matches a person's uuid or their external identifiers."
              : "People appear here once a face has been enrolled against them."
          }
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Person</th>
                <th scope="col">External identifiers</th>
                <th scope="col" className="text-right">Samples</th>
                <th scope="col" className="text-right">Embedded</th>
                <th scope="col">First seen</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((person) => (
                <tr key={person.person_uuid} className="hover:bg-surface-raised/50">
                  <td>
                    <Link
                      href={`/persons/${person.person_uuid}`}
                      className="identifier text-accent hover:underline"
                    >
                      {shortId(person.person_uuid)}
                    </Link>
                  </td>
                  <td>
                    {person.identifiers.length === 0 ? (
                      <span className="text-ink-faint">none</span>
                    ) : (
                      <span className="flex flex-wrap gap-1.5">
                        {person.identifiers.map((identifier) => (
                          <span
                            key={`${identifier.source}:${identifier.kind}:${identifier.value}`}
                            className="identifier rounded border border-line px-1.5 py-0.5"
                          >
                            {identifier.source}:{identifier.kind}={identifier.value}
                          </span>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="text-right">{person.sample_count}</td>
                  <td className="text-right">
                    {person.processed_count < person.sample_count ? (
                      <StatusBadge tone="review">
                        {person.processed_count}/{person.sample_count}
                      </StatusBadge>
                    ) : (
                      person.processed_count
                    )}
                  </td>
                  <td className="text-ink-muted">{formatTime(person.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination page={page} basePath="/persons" extra={search ? { search } : {}} />
    </>
  );
}
