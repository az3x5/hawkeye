"use client";

import { ImageUp, Loader2, RotateCcw, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { identifyAction, type IdentifyResult } from "@/app/identify/actions";
import { ScoreScale } from "@/components/score-scale";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import { describeOutcome, formatMargin, formatScore, isNarrowMargin, shortId } from "@/lib/format";
import type { Identification } from "@/lib/types";

const ACCEPTED = "image/jpeg,image/png,image/webp";

/**
 * Submit a face and see what the system proposes.
 *
 * The preview is a local object URL, never written to storage: biometric
 * material should not outlive the tab it was dropped into. The submitted image
 * goes to the API through a server action, so the credential stays on the
 * server and the browser proxy keeps its narrow allowlist.
 */
export function IdentifyConsole() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<IdentifyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  // Derived during render; the effect only revokes, which is the external
  // resource this component is actually responsible for. Object URLs leak
  // until revoked, and each one holds a face in memory.
  const preview = useMemo(() => (file === null ? null : URL.createObjectURL(file)), [file]);
  useEffect(() => {
    if (preview === null) return;
    return () => URL.revokeObjectURL(preview);
  }, [preview]);

  function choose(chosen: File | null) {
    setResult(null);
    setFile(chosen);
  }

  async function submit() {
    if (file === null || busy) return;
    setBusy(true);
    const body = new FormData();
    body.append("image", file);
    const outcome = await identifyAction(body);
    setResult(outcome);
    setBusy(false);
    if (!outcome.ok && outcome.signedOut) router.push("/sign-in");
  }

  function reset() {
    setFile(null);
    setResult(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
      <section className="panel p-4" aria-labelledby="query-heading">
        <h2 id="query-heading" className="mb-3 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
          Query image
        </h2>

        <label
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            choose(event.dataTransfer.files[0] ?? null);
          }}
          className={`flex aspect-square cursor-pointer items-center justify-center overflow-hidden rounded-md border border-dashed ${
            dragging ? "border-accent bg-accent/5" : "border-line-strong bg-surface-sunken"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            className="sr-only"
            onChange={(event) => choose(event.target.files?.[0] ?? null)}
          />
          {preview === null ? (
            <span className="px-6 text-center text-sm text-ink-muted">
              <ImageUp className="mx-auto mb-2 size-6 text-ink-faint" aria-hidden="true" />
              Drop a face image here, or click to choose
              <span className="mt-1 block text-xs text-ink-faint">JPEG, PNG or WebP</span>
            </span>
          ) : (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img src={preview} alt="The image about to be identified" className="size-full object-cover" />
          )}
        </label>

        {file !== null ? (
          <p className="mt-2 truncate text-xs text-ink-faint" title={file.name}>
            {file.name} · {(file.size / 1024).toFixed(0)} kB
          </p>
        ) : null}

        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => void submit()}
            disabled={file === null || busy}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Upload className="size-4" aria-hidden="true" />
            )}
            {busy ? "Identifying…" : "Identify"}
          </button>
          {file !== null ? (
            <button
              type="button"
              onClick={reset}
              className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-ink-muted hover:text-ink"
            >
              <RotateCcw className="size-4" aria-hidden="true" />
              Clear
            </button>
          ) : null}
        </div>

        <p className="mt-3 text-xs leading-relaxed text-ink-faint">
          The image is sent to the API and retained with the identification record so a
          reviewer can see what was submitted. It is never written to browser storage.
        </p>
      </section>

      <section aria-live="polite">
        {result === null ? (
          <div className="panel flex h-full min-h-64 items-center justify-center p-6 text-center">
            <p className="max-w-sm text-sm text-ink-muted">
              Submit an image to see who the system proposes, with the scores and the
              policy that produced the decision.
            </p>
          </div>
        ) : result.ok ? (
          <Decision identification={result.identification} preview={preview} />
        ) : (
          <ErrorState
            title={result.code === "rate_limited" ? "Too many requests" : "Could not identify"}
            message={result.message}
            code={result.code}
          />
        )}
      </section>
    </div>
  );
}

/** The proposal, its evidence, and the policy that produced it. */
function Decision({
  identification,
  preview,
}: {
  identification: Identification;
  preview: string | null;
}) {
  const { candidates, thresholds, margin, outcome } = identification;
  const best = candidates[0];

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge tone={toneForOutcome(outcome)}>{outcome}</StatusBadge>
          <p className="text-sm text-ink">{describeOutcome(outcome)}.</p>
        </div>

        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-ink-faint">Identification</dt>
            <dd className="identifier">{shortId(identification.identification_uuid)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-faint">Policy</dt>
            <dd className="identifier">{thresholds.policy_version}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-faint">Top similarity</dt>
            <dd className="tabular-nums">{formatScore(best?.score ?? null)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-faint">Margin</dt>
            <dd className="tabular-nums">{formatMargin(margin)}</dd>
          </div>
        </dl>

        {outcome === "review" ? (
          <p className="mt-3 rounded-md border-l-2 border-review bg-review/10 px-3 py-2 text-sm text-review">
            This proposal is waiting for a human decision.{" "}
            <Link href={`/review/${identification.identification_uuid}`} className="underline">
              Open it in review
            </Link>
            .
          </p>
        ) : null}

        {isNarrowMargin(margin) ? (
          <p className="mt-2 text-xs text-ink-faint">
            The top two candidates are {formatMargin(margin)} apart.
          </p>
        ) : null}
      </div>

      {candidates.length === 0 ? (
        <div className="panel px-6 py-10 text-center">
          <p className="text-sm font-semibold text-ink">No candidates</p>
          <p className="mt-1 text-sm text-ink-muted">
            Nobody in the gallery was close enough to report.
          </p>
        </div>
      ) : (
        <div className="panel p-4">
          <h2 className="mb-3 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
            Candidates
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {preview !== null ? (
              <figure className="m-0 space-y-2">
                <figcaption className="text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
                  Submitted
                </figcaption>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={preview}
                  alt="The image that was identified"
                  className="aspect-square w-full rounded-md border border-line object-cover"
                />
              </figure>
            ) : null}

            {candidates.map((candidate, index) => (
              <figure key={candidate.face_sample_uuid} className="m-0 space-y-2">
                <figcaption className="text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
                  {index === 0 ? "Best match" : `Candidate ${index + 1}`}
                </figcaption>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`/api/v1/face-samples/${candidate.face_sample_uuid}/image`}
                  alt={`Enrolled sample for person ${shortId(candidate.person_uuid)}`}
                  className="aspect-square w-full rounded-md border border-line bg-surface-sunken object-cover"
                />
                <div className="text-sm text-ink-muted">
                  person <span className="identifier">{shortId(candidate.person_uuid)}</span> ·{" "}
                  {candidate.sample_count} sample{candidate.sample_count === 1 ? "" : "s"}
                </div>
                <ScoreScale score={candidate.score} thresholds={thresholds} />
                <div className="text-sm text-ink-muted">
                  similarity{" "}
                  <span className="font-semibold text-ink">{formatScore(candidate.score)}</span>
                </div>
              </figure>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
