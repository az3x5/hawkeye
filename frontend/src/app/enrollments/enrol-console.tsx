"use client";

import { ImageUp, Loader2, RotateCcw, Search, UploadCloud } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { enrolAction, sampleStateAction } from "@/app/enrollments/actions";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import { formatTime, shortId } from "@/lib/format";
import type { FaceSample } from "@/lib/types";

const ACCEPTED = "image/jpeg,image/png,image/webp";
const FIELD = "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink";
const LABEL = "block space-y-1.5 text-sm text-ink-muted";

/** How often a pending sample is re-read, and for how long. */
const POLL_MS = 2000;
const POLL_LIMIT = 30;

interface Tracked {
  sample: FaceSample;
  /** False when the submission repeated an earlier one. */
  created: boolean;
}

/**
 * Enrol a face and follow it through the pipeline.
 *
 * Enrolment answers immediately with `pending`: detection and embedding happen
 * in a worker. The samples below are the ones submitted or looked up in this
 * browser session — the API has no list endpoint, so this is not a register of
 * everything enrolled, and it is labelled accordingly.
 */
export function EnrolConsole({ canSeeImages }: { canSeeImages: boolean }) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [tracked, setTracked] = useState<Tracked[]>([]);
  const [lookup, setLookup] = useState("");

  // Derived, and revoked when it changes: an object URL holds a face in memory
  // until released.
  const preview = useMemo(() => (file === null ? null : URL.createObjectURL(file)), [file]);
  useEffect(() => {
    if (preview === null) return;
    return () => URL.revokeObjectURL(preview);
  }, [preview]);

  const remember = useCallback((sample: FaceSample, created: boolean) => {
    setTracked((current) => [
      { sample, created },
      ...current.filter((entry) => entry.sample.face_sample_uuid !== sample.face_sample_uuid),
    ]);
  }, []);

  // Follow anything still pending. Detection and embedding usually finish in a
  // second or two; the cap stops a stuck worker polling forever.
  const pending = tracked.filter((entry) => entry.sample.processing_state === "pending");
  useEffect(() => {
    if (pending.length === 0) return;
    let rounds = 0;
    const timer = setInterval(() => {
      rounds += 1;
      if (rounds > POLL_LIMIT) {
        clearInterval(timer);
        return;
      }
      for (const entry of pending) {
        void sampleStateAction(entry.sample.face_sample_uuid).then((result) => {
          if (result.ok) remember(result.sample, entry.created);
        });
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [pending, remember]);

  async function submit(formData: FormData) {
    setBusy(true);
    setError(null);
    const result = await enrolAction(formData);
    setBusy(false);

    if (!result.ok) {
      setError({ code: result.code, message: result.message });
      if (result.signedOut) router.push("/sign-in");
      return;
    }
    remember(result.enrolment.sample, result.enrolment.created);
    setFile(null);
    formRef.current?.reset();
  }

  async function find() {
    const id = lookup.trim();
    if (id === "") return;
    setError(null);
    const result = await sampleStateAction(id);
    if (result.ok) {
      remember(result.sample, true);
      setLookup("");
    } else {
      setError({ code: result.code, message: result.message });
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[24rem_1fr]">
      <section className="panel p-4" aria-labelledby="enrol-heading">
        <h2 id="enrol-heading" className="mb-3 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
          Enrol a face
        </h2>

        <form ref={formRef} action={submit} className="space-y-3">
          <label
            className="flex aspect-square cursor-pointer items-center justify-center overflow-hidden rounded-md border border-dashed border-line-strong bg-surface-sunken"
          >
            <input
              ref={inputRef}
              type="file"
              name="image"
              accept={ACCEPTED}
              className="sr-only"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              required
            />
            {preview === null ? (
              <span className="px-6 text-center text-sm text-ink-muted">
                <ImageUp className="mx-auto mb-2 size-6 text-ink-faint" aria-hidden="true" />
                Choose a face image
              </span>
            ) : (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img src={preview} alt="The image about to be enrolled" className="size-full object-cover" />
            )}
          </label>

          <label className={LABEL}>
            Source
            <input type="text" name="source" placeholder="crm" className={FIELD} required />
          </label>
          <label className={LABEL}>
            External id
            <input type="text" name="external_id" placeholder="alice" className={FIELD} />
          </label>
          <label className={LABEL}>
            Local id
            <input type="text" name="local_id" className={FIELD} />
          </label>
          <label className={LABEL}>
            Captured at
            <input type="datetime-local" name="captured_at" className={FIELD} />
          </label>
          <p className="text-xs text-ink-faint">
            Identifiers are scoped by source, and at least one is required so a repeated
            submission can be recognised as the same person.
          </p>

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
            >
              {busy ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <UploadCloud className="size-4" aria-hidden="true" />
              )}
              {busy ? "Enrolling…" : "Enrol"}
            </button>
            {file !== null ? (
              <button
                type="button"
                onClick={() => {
                  setFile(null);
                  formRef.current?.reset();
                }}
                className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-ink-muted hover:text-ink"
              >
                <RotateCcw className="size-4" aria-hidden="true" />
                Clear
              </button>
            ) : null}
          </div>
        </form>
      </section>

      <section className="space-y-4">
        <div className="panel p-4">
          <h2 className="mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
            Look up a sample
          </h2>
          <div className="flex gap-2">
            <input
              type="text"
              value={lookup}
              onChange={(event) => setLookup(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void find();
              }}
              placeholder="face sample uuid"
              aria-label="Face sample uuid"
              className={`${FIELD} font-mono`}
            />
            <button
              type="button"
              onClick={() => void find()}
              className="inline-flex shrink-0 items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-ink-muted hover:text-ink"
            >
              <Search className="size-4" aria-hidden="true" />
              Find
            </button>
          </div>
        </div>

        {error === null ? null : (
          <ErrorState title="Enrolment failed" message={error.message} code={error.code} />
        )}

        <TrackedSamples entries={tracked} canSeeImages={canSeeImages} />
      </section>
    </div>
  );
}

/** Samples submitted or looked up in this session — not a register. */
function TrackedSamples({
  entries,
  canSeeImages,
}: {
  entries: Tracked[];
  canSeeImages: boolean;
}) {
  if (entries.length === 0) {
    return (
      <div className="panel px-6 py-10 text-center">
        <p className="text-sm font-semibold text-ink">Nothing tracked yet</p>
        <p className="mx-auto mt-1 max-w-prose text-sm text-ink-muted">
          Samples you enrol or look up appear here while this page is open. The API has no
          endpoint for listing enrolled samples, so this is not a register of everything
          enrolled.
        </p>
      </div>
    );
  }

  return (
    <div className="panel overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            {canSeeImages ? <th scope="col" className="w-16">Face</th> : null}
            <th scope="col">Sample</th>
            <th scope="col">Person</th>
            <th scope="col">Source</th>
            <th scope="col">State</th>
            <th scope="col">Enrolled</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(({ sample, created }) => (
            <tr key={sample.face_sample_uuid}>
              {canSeeImages ? (
                <td>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`/api/v1/face-samples/${sample.face_sample_uuid}/image`}
                    alt=""
                    aria-hidden="true"
                    className="size-11 rounded border border-line object-cover"
                  />
                </td>
              ) : null}
              <td className="identifier">{shortId(sample.face_sample_uuid)}</td>
              <td className="identifier">{shortId(sample.person_uuid)}</td>
              <td className="text-ink-muted">{sample.source}</td>
              <td>
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge tone={toneForOutcome(sample.processing_state)}>
                    {sample.processing_state === "pending" ? (
                      <>
                        <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                        pending
                      </>
                    ) : (
                      sample.processing_state
                    )}
                  </StatusBadge>
                  {!created ? (
                    <StatusBadge tone="neutral">already enrolled</StatusBadge>
                  ) : null}
                </div>
                {sample.failure_reason !== null ? (
                  <p className="mt-1 text-xs text-reject">{sample.failure_reason}</p>
                ) : null}
              </td>
              <td className="text-ink-muted">{formatTime(sample.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
