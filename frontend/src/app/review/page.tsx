import { ClipboardCheck } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge } from "@/components/states/status-badge";
import { ApiError, NotAuthenticatedError, fetchReviewQueue } from "@/lib/api";
import { formatAge, formatScore, isNarrowMargin, shortId } from "@/lib/format";
import type { ReviewQueue } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Proposals the system declined to decide alone.
 *
 * Backed by real endpoints, so this screen shows real data. Oldest first: a
 * review queue is a backlog, and the item waiting longest is the one most in
 * need of attention.
 */
export default async function ReviewQueuePage() {
  let queue: ReviewQueue;
  try {
    queue = await fetchReviewQueue();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    return (
      <>
        <PageHeader title="Review" />
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
        title="Review"
        description="Proposals the system declined to decide on its own. Scores are cosine similarities, not probabilities."
        actions={
          queue.count > 0 ? (
            <StatusBadge tone="review">{queue.count} awaiting</StatusBadge>
          ) : null
        }
      />

      {queue.items.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="Nothing is waiting for review"
          description="Every recent identification was decided within the current policy."
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col" className="w-20">
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
              {queue.items.map((item) => (
                <tr key={item.identification_uuid} className="hover:bg-surface-raised/50">
                  <td>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`/api/v1/identifications/${item.identification_uuid}/image`}
                      alt=""
                      aria-hidden="true"
                      className="size-12 rounded border border-line object-cover"
                    />
                  </td>
                  <td>
                    <Link
                      href={`/review/${item.identification_uuid}`}
                      className="identifier text-accent hover:underline"
                    >
                      {shortId(item.identification_uuid)}
                    </Link>
                  </td>
                  <td className="text-ink-muted">{formatAge(item.created_at)}</td>
                  <td className="text-right font-semibold">
                    {formatScore(item.best_score)}
                  </td>
                  <td className="text-right">
                    {isNarrowMargin(item.margin) ? (
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
