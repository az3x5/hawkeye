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
      <h2>Comparison</h2>
      <div className="compare">
        <figure className="face-panel" style={{ margin: 0 }}>
          <figcaption className="label">Submitted</figcaption>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`/api/v1/identifications/${identification.identification_uuid}/image`}
            alt="The face image submitted for identification"
          />
          <div className="caption">The image being identified</div>
        </figure>

        {candidate === undefined ? (
          <div className="card empty">
            <strong>No candidates</strong>
            The system matched nobody in the gallery.
          </div>
        ) : (
          <figure className="face-panel" style={{ margin: 0 }}>
            <figcaption className="label">
              {selected === 0 ? "Best match" : `Candidate ${selected + 1}`}
            </figcaption>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api/v1/face-samples/${candidate.face_sample_uuid}/image`}
              alt={`Enrolled sample for person ${shortId(candidate.person_uuid)}`}
            />
            <div className="caption">
              person <strong className="mono">{shortId(candidate.person_uuid)}</strong> ·{" "}
              {candidate.sample_count} sample{candidate.sample_count === 1 ? "" : "s"} on file
            </div>
            <ScoreScale score={candidate.score} thresholds={thresholds} />
            <div className="caption">
              similarity <strong>{formatScore(candidate.score)}</strong> under policy{" "}
              <span className="mono">{thresholds.policy_version}</span>
            </div>
          </figure>
        )}
      </div>

      {candidates.length > 1 ? (
        <>
          <h2>
            Other candidates <kbd>←</kbd> <kbd>→</kbd>
          </h2>
          <div className="strip">
            {candidates.map((entry, index) => (
              <button
                key={entry.face_sample_uuid}
                type="button"
                aria-pressed={index === selected}
                onClick={() => setSelected(index)}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`/api/v1/face-samples/${entry.face_sample_uuid}/image`}
                  alt={`Candidate ${index + 1}, person ${shortId(entry.person_uuid)}`}
                />
                <span className="strip-score">{formatScore(entry.score)}</span>
              </button>
            ))}
          </div>
          {isNarrowMargin(margin) ? (
            <p className="notice warn" style={{ marginTop: "0.75rem" }}>
              The top two candidates are {formatMargin(margin)} apart. Compare them both before
              deciding.
            </p>
          ) : null}
        </>
      ) : null}

      {decidable ? (
        <>
          <h2>Your decision</h2>
          <form
            className="review card padded"
            onSubmit={(event) => {
              event.preventDefault();
            }}
          >
            <label>
              Note (optional)
              <textarea
                name="note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="What made this clear, or unclear?"
                maxLength={2000}
              />
            </label>

            {error === null ? null : <p className="notice error">{error}</p>}

            <div className="actions">
              <button
                type="button"
                className="primary"
                disabled={submitting}
                onClick={() => void submit("confirmed")}
              >
                Confirm match <kbd>C</kbd>
              </button>
              <button
                type="button"
                className="danger"
                disabled={submitting}
                onClick={() => void submit("rejected")}
              >
                Reject match <kbd>R</kbd>
              </button>
            </div>
            <p className="notice">
              Recorded against the identity you signed in with, in an append-only audit log, and
              cannot be changed afterwards.
            </p>
          </form>
        </>
      ) : null}
    </>
  );
}
