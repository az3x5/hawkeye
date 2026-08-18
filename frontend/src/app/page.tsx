import Link from "next/link";
import { ApiError, fetchReviewQueue } from "@/lib/api";
import { formatMargin, formatScore, formatTime, isNarrowMargin, shortId } from "@/lib/format";
import type { ReviewQueue } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  let queue: ReviewQueue;
  try {
    queue = await fetchReviewQueue();
  } catch (error) {
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
        Proposals the system declined to decide on its own, oldest first. Scores are cosine
        similarities, not probabilities.
      </p>

      {queue.items.length === 0 ? (
        <p className="card">Nothing is waiting for review.</p>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th scope="col">Identification</th>
                <th scope="col">Submitted</th>
                <th scope="col" className="numeric">Top score</th>
                <th scope="col" className="numeric">Margin</th>
                <th scope="col" className="numeric">Candidates</th>
                <th scope="col">Policy</th>
              </tr>
            </thead>
            <tbody>
              {queue.items.map((item) => (
                <tr key={item.identification_uuid}>
                  <td>
                    <Link href={`/review/${item.identification_uuid}`} className="mono">
                      {shortId(item.identification_uuid)}
                    </Link>
                  </td>
                  <td>{formatTime(item.created_at)}</td>
                  <td className="numeric">{formatScore(item.best_score)}</td>
                  <td className="numeric">
                    {formatMargin(item.margin)}
                    {isNarrowMargin(item.margin) ? " (narrow)" : ""}
                  </td>
                  <td className="numeric">{item.candidate_count}</td>
                  <td className="mono">{item.policy_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
