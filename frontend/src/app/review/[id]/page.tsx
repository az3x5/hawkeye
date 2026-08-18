import Link from "next/link";
import { notFound } from "next/navigation";
import { ReviewForm } from "@/app/review/[id]/review-form";
import { ApiError, fetchIdentification } from "@/lib/api";
import { formatMargin, formatScore, formatTime, isNarrowMargin, shortId } from "@/lib/format";
import type { Identification } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let identification: Identification;
  try {
    identification = await fetchIdentification(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    const message = error instanceof ApiError ? error.message : "The API is unreachable.";
    return (
      <>
        <h1>Identification</h1>
        <p className="notice error">Could not load this identification: {message}</p>
      </>
    );
  }

  const { thresholds, candidates, margin, outcome } = identification;
  const alreadyReviewed = identification.review_outcome !== null;

  return (
    <>
      <p>
        <Link href="/">← Review queue</Link>
      </p>
      <h1>
        Identification <span className="mono">{shortId(identification.identification_uuid)}</span>{" "}
        <span className={`badge ${outcome}`}>{outcome}</span>
      </h1>
      <p className="lede">
        The system proposes; you decide. Scores below are raw cosine similarities in [-1, 1] —
        they are not probabilities and not percentages.
      </p>

      <div className="card">
        <dl className="facts">
          <dt>Policy</dt>
          <dd className="mono">{thresholds.policy_version}</dd>
          <dt>Accept at</dt>
          <dd>{formatScore(thresholds.accept_at)}</dd>
          <dt>Review at</dt>
          <dd>{formatScore(thresholds.review_at)}</dd>
          <dt>Margin to runner-up</dt>
          <dd>
            {formatMargin(margin)}
            {isNarrowMargin(margin) ? " — the top two are close; look carefully" : ""}
          </dd>
        </dl>
      </div>

      <h2>Submitted image</h2>
      <div className="faces">
        <div className="face">
          <figure>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api/v1/identifications/${identification.identification_uuid}/image`}
              alt="The face image submitted for identification"
            />
            <figcaption>Query</figcaption>
          </figure>
        </div>
      </div>

      <h2>Candidates</h2>
      {candidates.length === 0 ? (
        <p className="card">The system matched nobody.</p>
      ) : (
        <div className="faces">
          {candidates.map((candidate, index) => (
            <div className="face" key={candidate.face_sample_uuid}>
              <figure>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`/api/v1/face-samples/${candidate.face_sample_uuid}/image`}
                  alt={`Best matching enrolled sample for person ${shortId(candidate.person_uuid)}`}
                />
                <figcaption>
                  {index === 0 ? "Best match · " : ""}
                  person <span className="mono">{shortId(candidate.person_uuid)}</span>
                  <br />
                  score {formatScore(candidate.score)} · {candidate.sample_count}{" "}
                  {candidate.sample_count === 1 ? "sample" : "samples"} seen
                </figcaption>
              </figure>
            </div>
          ))}
        </div>
      )}

      <h2>Your decision</h2>
      {alreadyReviewed ? (
        <div className="card">
          <dl className="facts">
            <dt>Outcome</dt>
            <dd>{identification.review_outcome}</dd>
            <dt>Reviewer</dt>
            <dd>{identification.reviewed_by}</dd>
            <dt>Recorded</dt>
            <dd>{formatTime(identification.reviewed_at)}</dd>
            <dt>Note</dt>
            <dd>{identification.review_note ?? "—"}</dd>
          </dl>
          <p className="notice">
            A recorded review cannot be changed. Raise a new identification if this needs
            revisiting.
          </p>
        </div>
      ) : outcome !== "review" ? (
        <p className="notice">
          This proposal was decided <strong>{outcome}</strong> automatically and was never sent
          for review, so there is nothing here for you to confirm.
        </p>
      ) : (
        <ReviewForm identificationId={identification.identification_uuid} />
      )}
    </>
  );
}
