import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ReviewWorkspace } from "@/app/review/[id]/review-workspace";
import { ApiError, NotAuthenticatedError, fetchIdentification } from "@/lib/api";
import { describeOutcome, formatTime, shortId } from "@/lib/format";
import type { Identification } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let identification: Identification;
  try {
    identification = await fetchIdentification(id);
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 404) notFound();
    const message = error instanceof ApiError ? error.message : "The API is unreachable.";
    return (
      <>
        <h1>Identification</h1>
        <p className="notice error">Could not load this identification: {message}</p>
      </>
    );
  }

  const reviewed = identification.review_outcome !== null;

  return (
    <>
      <Link href="/" className="backlink">
        ← Review queue
      </Link>

      <h1>
        Identification <span className="mono">{shortId(identification.identification_uuid)}</span>{" "}
        <span className={`badge ${identification.outcome}`}>{identification.outcome}</span>
      </h1>
      <p className="lede">
        {describeOutcome(identification.outcome)}. Scores are raw cosine similarities in
        [-1, 1] — not probabilities, and not percentages.
      </p>

      <ReviewWorkspace identification={identification} />

      {/* The decision form lives in the workspace, beside the comparison it
          depends on. This section is only for outcomes already settled. */}
      {reviewed ? (
        <>
        <h2>Decision</h2>
        <div className="card padded">
          <dl className="facts">
            <dt>Outcome</dt>
            <dd>
              <span
                className={`badge ${
                  identification.review_outcome === "confirmed" ? "accept" : "reject"
                }`}
              >
                {identification.review_outcome}
              </span>
            </dd>
            <dt>Reviewer</dt>
            <dd>{identification.reviewed_by}</dd>
            <dt>Recorded</dt>
            <dd>{formatTime(identification.reviewed_at)}</dd>
            <dt>Note</dt>
            <dd>{identification.review_note ?? "—"}</dd>
          </dl>
          <p className="notice" style={{ marginTop: "1rem" }}>
            A recorded review cannot be changed. Raise a new identification if this needs
            revisiting.
          </p>
        </div>
        </>
      ) : identification.outcome !== "review" ? (
        <>
          <h2>Decision</h2>
          <p className="notice">
            This proposal was decided <strong>{identification.outcome}</strong> automatically
            and was never sent for review, so there is nothing here for you to confirm.
          </p>
        </>
      ) : null}
    </>
  );
}
