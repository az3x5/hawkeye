"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ScoreScale } from "@/components/score-scale";
import { formatMargin, formatScore, isNarrowMargin, shortId } from "@/lib/format";
import type { Identification, ReviewOutcome } from "@/lib/types";

/**
 * The reviewer's working surface.
 *
 * The task is comparing two faces, so the query and the candidate sit side by
 * side at the same size. Everything else — the candidate strip, the score
 * scale, the decision buttons — is arranged around that comparison rather than
 * stacked above and below it.
 */
export function ReviewWorkspace({ identification }: { identification: Identification }) {
  const router = useRouter();
  const { candidates, thresholds, margin } = identification;

  const [selected, setSelected] = useState(0);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [, startTransition] = useTransition();

  const decidable = identification.outcome === "review" && identification.review_outcome === null;
  const candidate = candidates[selected];

  const submit = useCallback(
    async (outcome: ReviewOutcome) => {
      if (!decidable || submitting) return;
      setSubmitting(true);
      setError(null);

      const response = await fetch(
        `/api/v1/identifications/${identification.identification_uuid}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ outcome, note: note.trim() === "" ? null : note }),
        },
      );

      if (!response.ok) {
        // Surface the API's own message: it explains *why* a review was refused
        // better than we could from here.
        const body: unknown = await response.json().catch(() => null);
        setError(
          typeof body === "object" && body !== null && "error" in body
            ? String((body as { error: { message: string } }).error.message)
            : `The review could not be recorded (HTTP ${response.status}).`,
        );
        setSubmitting(false);
        return;
      }
      startTransition(() => router.refresh());
    },
    [decidable, submitting, identification.identification_uuid, note, router],
  );

  // Someone working through a backlog should not have to reach for the mouse.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "ArrowRight") {
        setSelected((current) => Math.min(current + 1, candidates.length - 1));
      } else if (event.key === "ArrowLeft") {
        setSelected((current) => Math.max(current - 1, 0));
      } else if (event.key.toLowerCase() === "c") {
        void submit("confirmed");
      } else if (event.key.toLowerCase() === "r") {
        void submit("rejected");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [candidates.length, submit]);

  return (
    <>
      <h2 className="mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">Comparison</h2>
      <div className="grid gap-4 lg:grid-cols-2">
        <figure className="m-0 space-y-2">
          <figcaption className="text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">Submitted</figcaption>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`/api/v1/identifications/${identification.identification_uuid}/image`}
            alt="The face image submitted for identification"
            className="aspect-square w-full rounded-md border border-line bg-surface-sunken object-cover"
          />
          <div className="text-sm text-ink-muted">The image being identified</div>
        </figure>

        {candidate === undefined ? (
          <div className="panel px-6 py-12 text-center">
            <p className="text-sm font-semibold text-ink">No candidates</p>
            <p className="mt-1 text-sm text-ink-muted">
              The system matched nobody in the gallery.
            </p>
          </div>
        ) : (
          <figure className="m-0 space-y-2">
            <figcaption className="text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
              {selected === 0 ? "Best match" : `Candidate ${selected + 1}`}
            </figcaption>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api/v1/face-samples/${candidate.face_sample_uuid}/image`}
              alt={`Enrolled sample for person ${shortId(candidate.person_uuid)}`}
              className="aspect-square w-full rounded-md border border-line bg-surface-sunken object-cover"
            />
            <div className="text-sm text-ink-muted">
              person <span className="identifier">{shortId(candidate.person_uuid)}</span> ·{" "}
              {candidate.sample_count} sample{candidate.sample_count === 1 ? "" : "s"} on file
            </div>
            <ScoreScale score={candidate.score} thresholds={thresholds} />
            <div className="text-sm text-ink-muted">
              similarity{" "}
              <span className="font-semibold text-ink">{formatScore(candidate.score)}</span>{" "}
              under policy <span className="identifier">{thresholds.policy_version}</span>
            </div>
          </figure>
        )}
      </div>

      {candidates.length > 1 ? (
        <>
          <h2 className="mt-6 mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
            Other candidates <kbd className="ml-1 rounded bg-surface-raised px-1 py-0.5">←</kbd>{" "}
            <kbd className="rounded bg-surface-raised px-1 py-0.5">→</kbd>
          </h2>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {candidates.map((entry, index) => (
              <button
                key={entry.face_sample_uuid}
                type="button"
                aria-pressed={index === selected}
                onClick={() => setSelected(index)}
                className={`shrink-0 space-y-1 rounded-md border p-1.5 ${
                  index === selected ? "border-accent" : "border-line hover:border-line-strong"
                }`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`/api/v1/face-samples/${entry.face_sample_uuid}/image`}
                  alt={`Candidate ${index + 1}, person ${shortId(entry.person_uuid)}`}
                  className="size-16 rounded border border-line object-cover"
                />
                <span className="block text-center text-xs text-ink-muted tabular-nums">
                  {formatScore(entry.score)}
                </span>
              </button>
            ))}
          </div>
          {isNarrowMargin(margin) ? (
            <p className="mt-3 rounded-md border-l-2 border-review bg-review/10 px-3 py-2 text-sm text-review">
              The top two candidates are {formatMargin(margin)} apart. Compare them both before
              deciding.
            </p>
          ) : null}
        </>
      ) : null}

      {decidable ? (
        <>
          <h2 className="mt-6 mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
            Your decision
          </h2>
          <form
            className="panel max-w-2xl space-y-3 p-4"
            onSubmit={(event) => {
              event.preventDefault();
            }}
          >
            <label className="block space-y-1.5 text-sm text-ink-muted">
              Note (optional)
              <textarea
                className="min-h-20 w-full rounded-md border border-line bg-surface-sunken p-2 text-sm text-ink"
                name="note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="What made this clear, or unclear?"
                maxLength={2000}
              />
            </label>

            {error === null ? null : (
              <p className="rounded-md border-l-2 border-reject bg-reject/10 px-3 py-2 text-sm text-reject">
                {error}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded-md bg-accept px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
                disabled={submitting}
                onClick={() => void submit("confirmed")}
              >
                Confirm match <kbd className="ml-1.5 opacity-70">C</kbd>
              </button>
              <button
                type="button"
                className="rounded-md bg-reject px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
                disabled={submitting}
                onClick={() => void submit("rejected")}
              >
                Reject match <kbd className="ml-1.5 opacity-70">R</kbd>
              </button>
            </div>
            <p className="rounded-md border-l-2 border-line-strong bg-surface-raised px-3 py-2 text-sm text-ink-muted">
              Recorded against the identity you signed in with, in an append-only audit log, and
              cannot be changed afterwards.
            </p>
          </form>
        </>
      ) : null}
    </>
  );
}
