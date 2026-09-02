"use client";

import { FileAudio, FileImage, Loader2 } from "lucide-react";
import { useState } from "react";
import type { DhivehiInference } from "@/lib/types";

type Tool = "speech" | "ocr";

export function MediaLanguageTools() {
  const [busy, setBusy] = useState<Tool | null>(null);
  const [result, setResult] = useState<DhivehiInference | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(tool: Tool, file: File | undefined) {
    if (file === undefined) return;
    setBusy(tool);
    setError(null);
    setResult(null);
    const body = new FormData();
    body.set("file", file);
    const path = tool === "speech" ? "/api/v1/nlp/speech/transcribe" : "/api/v1/nlp/ocr";
    try {
      const response = await fetch(path, { method: "POST", body });
      const payload = (await response.json()) as DhivehiInference & {
        error?: { message?: string };
      };
      if (!response.ok) throw new Error(payload.error?.message ?? `Request failed (${response.status})`);
      setResult(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The model request failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel mt-4" aria-labelledby="media-language-heading">
      <div className="border-b border-line px-4 py-3">
        <h2 id="media-language-heading" className="text-sm font-semibold text-ink">
          Voice and image text
        </h2>
        <p className="mt-0.5 text-xs text-ink-faint">
          Transcribe Dhivehi speech or recognize a cropped line of Thaana text.
        </p>
      </div>
      <div className="grid gap-3 p-4 md:grid-cols-2">
        <UploadControl
          accept="audio/wav,audio/flac,audio/ogg,.wav,.flac,.ogg"
          busy={busy === "speech"}
          icon={<FileAudio className="size-5" aria-hidden="true" />}
          label="Transcribe audio"
          onFile={(file) => submit("speech", file)}
        />
        <UploadControl
          accept="image/png,image/jpeg,image/webp"
          busy={busy === "ocr"}
          icon={<FileImage className="size-5" aria-hidden="true" />}
          label="Read Thaana image"
          onFile={(file) => submit("ocr", file)}
        />
      </div>
      {error ? <p className="border-t border-line px-4 py-3 text-sm text-reject">{error}</p> : null}
      {result ? (
        <div className="border-t border-line px-4 py-4">
          <p className="whitespace-pre-wrap text-lg leading-8 text-ink" dir="auto">
            {result.text}
          </p>
          <p className="mt-3 text-xs text-ink-faint">
            {result.model} · {result.model_revision.slice(0, 8)}
          </p>
          <p className="mt-1 text-xs text-review">{result.limitation}</p>
        </div>
      ) : null}
    </section>
  );
}

function UploadControl({
  accept,
  busy,
  icon,
  label,
  onFile,
}: {
  accept: string;
  busy: boolean;
  icon: React.ReactNode;
  label: string;
  onFile: (file: File | undefined) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-3 rounded-md border border-line bg-surface-sunken px-4 py-4 text-sm font-medium text-ink">
      {busy ? <Loader2 className="size-5 animate-spin" aria-hidden="true" /> : icon}
      <span>{busy ? "Model is processing…" : label}</span>
      <input
        className="sr-only"
        type="file"
        accept={accept}
        disabled={busy}
        onChange={(event) => {
          void onFile(event.target.files?.[0]);
          event.currentTarget.value = "";
        }}
      />
    </label>
  );
}
