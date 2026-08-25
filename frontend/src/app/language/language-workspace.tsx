"use client";

import { ArrowLeftRight, Check, Clipboard, Loader2, ScanText } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { normalizeAction, transliterateAction } from "@/app/language/actions";
import { StatusBadge } from "@/components/states/status-badge";
import type {
  LanguageNormalization,
  LanguageTransliteration,
  TransliterationDirection,
} from "@/lib/types";

const FIELD =
  "min-h-56 w-full resize-y rounded-md border border-line bg-surface-sunken px-3 py-3 text-base leading-7 text-ink outline-none focus:border-accent";

export function LanguageWorkspace() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [direction, setDirection] =
    useState<TransliterationDirection>("latin_to_thaana");
  const [normalization, setNormalization] = useState<LanguageNormalization | null>(null);
  const [transliteration, setTransliteration] = useState<LanguageTransliteration | null>(null);
  const [busy, setBusy] = useState<"normalize" | "transliterate" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function analyze() {
    setBusy("normalize");
    setError(null);
    const result = await normalizeAction(text);
    setBusy(null);
    if (!result.ok) {
      setError(result.message);
      if (result.signedOut) router.replace("/sign-in");
      return;
    }
    setNormalization(result.normalization);
  }

  async function convert() {
    setBusy("transliterate");
    setError(null);
    setCopied(false);
    const result = await transliterateAction(text, direction);
    setBusy(null);
    if (!result.ok) {
      setError(result.message);
      if (result.signedOut) router.replace("/sign-in");
      return;
    }
    setTransliteration(result.transliteration);
  }

  async function copyOutput() {
    if (transliteration === null) return;
    await navigator.clipboard.writeText(transliteration.output);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)]">
      <section className="panel p-4" aria-labelledby="language-input-heading">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="language-input-heading" className="text-sm font-semibold text-ink">
              Language workspace
            </h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              Thaana, Romanized Dhivehi, English, or mixed text · 20,000 characters maximum
            </p>
          </div>
          <span className="text-xs tabular-nums text-ink-faint">
            {text.length.toLocaleString()} / 20,000
          </span>
        </div>

        <textarea
          value={text}
          onChange={(event) => setText(event.target.value.slice(0, 20_000))}
          placeholder="ތާނަ، Romanized Dhivehi, or mixed English text…"
          className={FIELD}
          dir="auto"
          spellCheck={false}
        />

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={analyze}
            disabled={busy !== null || text.trim() === ""}
            className="inline-flex items-center gap-2 rounded-md border border-line-strong bg-surface px-4 py-2 text-sm font-medium text-ink disabled:opacity-50"
          >
            {busy === "normalize" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <ScanText className="size-4" aria-hidden="true" />
            )}
            Analyze scripts
          </button>
          <button
            type="button"
            onClick={convert}
            disabled={busy !== null || text.trim() === ""}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {busy === "transliterate" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <ArrowLeftRight className="size-4" aria-hidden="true" />
            )}
            Transliterate
          </button>
        </div>

        <fieldset className="mt-4 border-t border-line pt-4">
          <legend className="mb-2 text-xs font-medium text-ink-muted">Direction</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            <DirectionOption
              checked={direction === "latin_to_thaana"}
              label="Latin → Thaana"
              value="latin_to_thaana"
              onChange={setDirection}
            />
            <DirectionOption
              checked={direction === "thaana_to_latin"}
              label="Thaana → Latin"
              value="thaana_to_latin"
              onChange={setDirection}
            />
          </div>
        </fieldset>

        {error ? (
          <p className="mt-4 rounded-md border border-reject/30 bg-reject/10 px-3 py-2 text-sm text-reject">
            {error}
          </p>
        ) : null}
      </section>

      <div className="space-y-4">
        <section className="panel" aria-labelledby="transliteration-heading">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <h2 id="transliteration-heading" className="text-sm font-semibold text-ink">
                Transliteration
              </h2>
              {transliteration ? (
                <p className="mt-0.5 text-xs text-ink-faint">{transliteration.model_version}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={copyOutput}
              disabled={transliteration === null}
              className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-muted disabled:opacity-40"
            >
              {copied ? <Check className="size-3.5" /> : <Clipboard className="size-3.5" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <div className="min-h-40 whitespace-pre-wrap px-4 py-4 text-lg leading-8 text-ink" dir="auto">
            {transliteration?.output ?? (
              <span className="text-sm text-ink-faint">The converted text will appear here.</span>
            )}
          </div>
          {transliteration?.warnings.map((warning) => (
            <p key={warning} className="border-t border-line px-4 py-2 text-xs text-review">
              {warning}
            </p>
          ))}
        </section>

        <section className="panel" aria-labelledby="analysis-heading">
          <div className="border-b border-line px-4 py-3">
            <h2 id="analysis-heading" className="text-sm font-semibold text-ink">
              Script analysis
            </h2>
          </div>
          {normalization === null ? (
            <p className="px-4 py-6 text-sm text-ink-faint">Analyze text to see its script spans.</p>
          ) : (
            <div className="space-y-3 p-4">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-ink-faint">Primary composition</span>
                <StatusBadge tone="info">{normalization.primary_script}</StatusBadge>
              </div>
              <div className="flex flex-wrap gap-1.5" dir="auto">
                {normalization.spans.map((span) => (
                  <span
                    key={`${span.start}-${span.end}`}
                    className="rounded border border-line bg-surface-sunken px-2 py-1 text-sm text-ink"
                    title={`${span.script} · offsets ${span.start}–${span.end}`}
                  >
                    {span.text === " " ? "␠" : span.text}
                    <span className="ml-1 text-[10px] text-ink-faint">{span.script}</span>
                  </span>
                ))}
              </div>
              <p className="text-xs text-ink-faint">{normalization.normalizer_version}</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function DirectionOption({
  checked,
  label,
  value,
  onChange,
}: {
  checked: boolean;
  label: string;
  value: TransliterationDirection;
  onChange: (value: TransliterationDirection) => void;
}) {
  return (
    <label
      className={`cursor-pointer rounded-md border px-3 py-2 text-sm ${
        checked ? "border-accent bg-accent/10 text-ink" : "border-line text-ink-muted"
      }`}
    >
      <input
        type="radio"
        name="direction"
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
        className="mr-2"
      />
      {label}
    </label>
  );
}
