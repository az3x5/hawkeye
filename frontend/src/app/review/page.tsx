import { ClipboardCheck } from "lucide-react";
import { redirect } from "next/navigation";
import { QueueTable } from "@/app/review/queue-table";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge } from "@/components/states/status-badge";
import { ApiError, NotAuthenticatedError, fetchReviewQueue } from "@/lib/api";
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
        description="Proposals the system declined to decide on its own, oldest first. Scores are cosine similarities, not probabilities."
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
        <QueueTable items={queue.items} />
      )}
    </>
  );
}
