"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { ReviewOutcome } from "@/lib/types";

/**
 * Records a reviewer's conclusion.
 *
 * The reviewer's identity is required because the decision is written to an
 * audit log: an unattributed judgement is not much use as evidence.
 */
export function ReviewForm({ identificationId }: { identificationId: string }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  async function submit(outcome: ReviewOutcome) {
    setError(null);
    const response = await fetch(`/api/v1/identifications/${identificationId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome, note: note.trim() === "" ? null : note }),
    });

    if (!response.ok) {
      // Surface the API's own message: it explains *why* a review was refused
      // (already reviewed, never sent for review) better than we could here.
      const body: unknown = await response.json().catch(() => null);
      const message =
        typeof body === "object" && body !== null && "error" in body
          ? String((body as { error: { message: string } }).error.message)
          : `The review could not be recorded (HTTP ${response.status}).`;
      setError(message);
      return;
    }
    startTransition(() => router.refresh());
  }

  const canSubmit = !pending;

  return (
    <form
      className="review card"
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
          disabled={!canSubmit}
          onClick={() => void submit("confirmed")}
        >
          Confirm match
        </button>
        <button
          type="button"
          className="danger"
          disabled={!canSubmit}
          onClick={() => void submit("rejected")}
        >
          Reject match
        </button>
      </div>
      <p className="notice">
        Your decision is recorded against the token you signed in with, in an append-only audit
        log, and cannot be changed afterwards.
      </p>
    </form>
  );
}
