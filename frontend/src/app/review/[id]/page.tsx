import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ReviewWorkspace } from "@/app/review/[id]/review-workspace";
import { ApiError, NotAuthenticatedError, fetchIdentification } from "@/lib/api";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
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
        <PageHeader title="Identification" />
        <ErrorState message={message} />
      </>
    );
  }

  const reviewed = identification.review_outcome !== null;

  return (
    <>
      <Link
        href="/review"
        className="mb-3 inline-block text-sm text-ink-muted hover:text-ink"
      >
        ← Review queue
      </Link>

      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold tracking-tight text-ink">Identification</h1>
        <span className="identifier">{shortId(identification.identification_uuid)}</span>
        <StatusBadge tone={toneForOutcome(identification.outcome)}>
          {identification.outcome}
        </StatusBadge>
      </div>
      <p className="mb-5 max-w-prose text-sm text-ink-muted">
        {describeOutcome(identification.outcome)}. Scores are raw cosine similarities in
        [-1, 1] — not probabilities, and not percentages.
      </p>

      <ReviewWorkspace identification={identification} />

      {/* The decision form lives in the workspace, beside the comparison it
          depends on. This section is only for outcomes already settled. */}
      {reviewed ? (
        <>
          <h2 className="mt-6 mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
            Decision
          </h2>
          <div className="panel max-w-2xl p-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-ink-faint">Outcome</dt>
              <dd>
                <StatusBadge tone={toneForOutcome(identification.review_outcome ?? "")}>
                  {identification.review_outcome}
                </StatusBadge>
              </dd>
              <dt className="text-ink-faint">Reviewer</dt>
              <dd className="text-ink">{identification.reviewed_by}</dd>
              <dt className="text-ink-faint">Recorded</dt>
              <dd className="identifier">{formatTime(identification.reviewed_at)}</dd>
              <dt className="text-ink-faint">Note</dt>
              <dd className="text-ink">{identification.review_note ?? "—"}</dd>
            </dl>
            <p className="mt-4 rounded-md border-l-2 border-line-strong bg-surface-raised px-3 py-2 text-sm text-ink-muted">
              A recorded review cannot be changed. Raise a new identification if this needs
              revisiting.
            </p>
          </div>
        </>
      ) : identification.outcome !== "review" ? (
        <>
          <h2 className="mt-6 mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
            Decision
          </h2>
          <p className="max-w-2xl rounded-md border-l-2 border-line-strong bg-surface-raised px-3 py-2 text-sm text-ink-muted">
            This proposal was decided <strong className="text-ink">{identification.outcome}</strong>{" "}
            automatically and was never sent for review, so there is nothing here for you to
            confirm.
          </p>
        </>
      ) : null}
    </>
  );
}
