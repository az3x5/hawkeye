import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import {
  ApiError,
  NotAuthenticatedError,
  fetchIdentificationHistory,
  fetchPerson,
} from "@/lib/api";
import { formatScore, formatTime, shortId } from "@/lib/format";
import type { PersonDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PersonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let person: PersonDetail;
  try {
    person = await fetchPerson(id);
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <>
        <PageHeader title="Person" />
        <ErrorState message={error instanceof ApiError ? error.message : "The API is unreachable."} />
      </>
    );
  }

  // Identifications that named this person. A failure here must not take the
  // person's own record down with it.
  const history = await fetchIdentificationHistory({ person_uuid: id, limit: 20 }).catch(
    () => null,
  );

  return (
    <>
      <Link href="/persons" className="mb-3 inline-block text-sm text-ink-muted hover:text-ink">
        ← Persons
      </Link>

      <PageHeader
        title="Person"
        description="Everything the system holds about one person."
      />

      <div className="space-y-4">
        <section className="panel">
          <div className="border-b border-line px-4 py-3">
            <h2 className="text-sm font-semibold text-ink">Record</h2>
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-4 py-3 sm:grid-cols-4">
            <div className="col-span-2">
              <dt className="text-xs text-ink-faint">Internal key</dt>
              <dd className="identifier break-all">{person.person_uuid}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">First seen</dt>
              <dd className="text-sm">{formatTime(person.created_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Samples</dt>
              <dd className="text-sm">
                {person.processed_count} embedded of {person.sample_count}
              </dd>
            </div>
            <div className="col-span-2 sm:col-span-4">
              <dt className="mb-1 text-xs text-ink-faint">External identifiers</dt>
              <dd className="flex flex-wrap gap-1.5">
                {person.identifiers.length === 0 ? (
                  <span className="text-sm text-ink-faint">none</span>
                ) : (
                  person.identifiers.map((identifier) => (
                    <span
                      key={`${identifier.source}:${identifier.kind}:${identifier.value}`}
                      className="identifier rounded border border-line px-1.5 py-0.5"
                    >
                      {identifier.source}:{identifier.kind}={identifier.value}
                    </span>
                  ))
                )}
              </dd>
            </div>
          </dl>
        </section>

        <section className="panel">
          <div className="border-b border-line px-4 py-3">
            <h2 className="text-sm font-semibold text-ink">
              Face samples
              <span className="ml-2 font-normal text-ink-faint">
                a person may have many
              </span>
            </h2>
          </div>
          {person.samples.length === 0 ? (
            <p className="px-4 py-6 text-sm text-ink-muted">No samples are enrolled.</p>
          ) : (
            <div className="grid gap-4 p-4 sm:grid-cols-3 lg:grid-cols-4">
              {person.samples.map((sample) => (
                <figure key={sample.face_sample_uuid} className="m-0 space-y-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`/api/v1/face-samples/${sample.face_sample_uuid}/image`}
                    alt={`Enrolled sample ${shortId(sample.face_sample_uuid)}`}
                    className="aspect-square w-full rounded-md border border-line bg-surface-sunken object-cover"
                  />
                  <figcaption className="space-y-1">
                    <span className="identifier block">{shortId(sample.face_sample_uuid)}</span>
                    <StatusBadge tone={toneForOutcome(sample.processing_state)}>
                      {sample.processing_state}
                    </StatusBadge>
                    <span className="block text-xs text-ink-faint">
                      {sample.source} · {formatTime(sample.created_at)}
                    </span>
                    {sample.failure_reason ? (
                      <span className="block text-xs text-reject">{sample.failure_reason}</span>
                    ) : null}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="border-b border-line px-4 py-3">
            <h2 className="text-sm font-semibold text-ink">Identifications naming this person</h2>
          </div>
          {history === null ? (
            <p className="px-4 py-6 text-sm text-ink-muted">History could not be read.</p>
          ) : history.items.length === 0 ? (
            <p className="px-4 py-6 text-sm text-ink-muted">
              No identification has proposed this person.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Identification</th>
                    <th scope="col">Outcome</th>
                    <th scope="col" className="text-right">Score</th>
                    <th scope="col">Review</th>
                    <th scope="col">When</th>
                  </tr>
                </thead>
                <tbody>
                  {history.items.map((record) => (
                    <tr key={record.identification_uuid}>
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
                      <td className="text-right">{formatScore(record.best_score)}</td>
                      <td>
                        {record.review_outcome === null ? (
                          <span className="text-ink-faint">—</span>
                        ) : (
                          <StatusBadge tone={toneForOutcome(record.review_outcome)}>
                            {record.review_outcome}
                          </StatusBadge>
                        )}
                      </td>
                      <td className="text-ink-muted">{formatTime(record.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
