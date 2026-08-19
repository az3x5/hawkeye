import Link from "next/link";
import { redirect } from "next/navigation";
import { ApiError, NotAuthenticatedError, fetchReviewQueue } from "@/lib/api";
import { formatAge, formatScore, isNarrowMargin, shortId } from "@/lib/format";
import type { ReviewQueue } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  let queue: ReviewQueue;
  try {
    queue = await fetchReviewQueue();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    const message = error instanceof ApiError ? error.message : "The API is unreachable.";
    return (
      <>
        <h1>Review queue</h1>
        <p className="notice error">Could not load the queue: {message}</p>
      </>
    );
  }

  return (
    <>
      <h1>Review queue</h1>
      <p className="lede">
        {queue.count === 0
          ? "Proposals the system declines to decide on its own arrive here."
          : `${queue.count} proposal${queue.count === 1 ? "" : "s"} the system declined to ` +
            "decide alone, longest-waiting first."}
      </p>

      {queue.items.length === 0 ? (
        <div className="card empty">
          <strong>Nothing is waiting for review</strong>
          Every recent identification was decided within the current policy.
        </div>
      ) : (
        <div className="queue">
          {queue.items.map((item) => (
            <Link
              key={item.identification_uuid}
              href={`/review/${item.identification_uuid}`}
              className="queue-item"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                className="thumb"
                src={`/api/v1/identifications/${item.identification_uuid}/image`}
                alt=""
                aria-hidden="true"
              />
              <div>
                <div className="headline">
                  {item.candidate_count === 0
                    ? "No candidates"
                    : `${item.candidate_count} candidate${item.candidate_count === 1 ? "" : "s"}`}
                  {isNarrowMargin(item.margin) ? (
                    <span className="badge review" style={{ marginLeft: "0.5rem" }}>
                      close call
                    </span>
                  ) : null}
                </div>
                <div className="meta">
                  <span>{formatAge(item.created_at)}</span>
                  <span className="sep">·</span>
                  <span className="mono">{shortId(item.identification_uuid)}</span>
                  <span className="sep">·</span>
                  <span className="mono">{item.policy_version}</span>
                </div>
              </div>
              <div className="trailing">
                <span className="score">{formatScore(item.best_score)}</span>
                <span className="badge plain">top similarity</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
