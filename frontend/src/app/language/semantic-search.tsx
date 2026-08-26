"use client";

import { Database, Loader2, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  createDocumentAction,
  searchDocumentsAction,
} from "@/app/language/actions";
import { StatusBadge } from "@/components/states/status-badge";
import type { LanguageDocument, LanguageSearchResponse } from "@/lib/types";

const INPUT =
  "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink outline-none focus:border-accent";

export function SemanticSearch() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("manual");
  const [documentText, setDocumentText] = useState("");
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [document, setDocument] = useState<LanguageDocument | null>(null);
  const [results, setResults] = useState<LanguageSearchResponse | null>(null);
  const [busy, setBusy] = useState<"index" | "search" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function indexDocument() {
    setBusy("index");
    setError(null);
    const result = await createDocumentAction({ title, source, text: documentText });
    setBusy(null);
    if (!result.ok) {
      setError(result.message);
      if (result.signedOut) router.replace("/sign-in");
      return;
    }
    setDocument(result.document);
  }

  async function searchDocuments() {
    setBusy("search");
    setError(null);
    const result = await searchDocumentsAction({ text: query, source: sourceFilter });
    setBusy(null);
    if (!result.ok) {
      setError(result.message);
      if (result.signedOut) router.replace("/sign-in");
      return;
    }
    setResults(result.search);
  }

  return (
    <section className="mt-4 grid gap-4 xl:grid-cols-2" aria-label="Semantic language search">
      <div className="panel p-4">
        <div className="mb-4 flex items-start gap-3">
          <Database className="mt-0.5 size-5 text-accent" aria-hidden="true" />
          <div>
            <h2 className="text-sm font-semibold text-ink">Index a document</h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              Text is normalized now and embedded asynchronously by the language worker.
            </p>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-ink-muted">
            Title
            <input
              className={`${INPUT} mt-1`}
              value={title}
              onChange={(event) => setTitle(event.target.value.slice(0, 256))}
              placeholder="Document title"
            />
          </label>
          <label className="text-xs font-medium text-ink-muted">
            Source
            <input
              className={`${INPUT} mt-1`}
              value={source}
              onChange={(event) => setSource(event.target.value.slice(0, 128))}
              placeholder="manual"
            />
          </label>
        </div>
        <textarea
          className={`${INPUT} mt-3 min-h-40 resize-y leading-6`}
          value={documentText}
          onChange={(event) => setDocumentText(event.target.value.slice(0, 100_000))}
          placeholder="Paste Thaana, Romanized Dhivehi, English, or mixed text…"
          dir="auto"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={indexDocument}
            disabled={busy !== null || !title.trim() || !source.trim() || !documentText.trim()}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {busy === "index" ? <Loader2 className="size-4 animate-spin" /> : <Database className="size-4" />}
            Add to index
          </button>
          <span className="text-xs tabular-nums text-ink-faint">
            {documentText.length.toLocaleString()} / 100,000
          </span>
        </div>
        {document ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
            <StatusBadge tone={document.processing_state === "failed" ? "reject" : "review"}>
              {document.processing_state}
            </StatusBadge>
            <span>{document.status === "already_exists" ? "Already indexed" : "Queued for embedding"}</span>
            <span className="identifier ml-auto">{document.document_uuid}</span>
          </div>
        ) : null}
      </div>

      <div className="panel p-4">
        <div className="mb-4 flex items-start gap-3">
          <Search className="mt-0.5 size-5 text-accent" aria-hidden="true" />
          <div>
            <h2 className="text-sm font-semibold text-ink">Semantic search</h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              Search across scripts by meaning, with an optional exact source filter.
            </p>
          </div>
        </div>
        <textarea
          className={`${INPUT} min-h-24 resize-y leading-6`}
          value={query}
          onChange={(event) => setQuery(event.target.value.slice(0, 20_000))}
          placeholder="What do you want to find?"
          dir="auto"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            className={`${INPUT} min-w-48 flex-1`}
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value.slice(0, 128))}
            placeholder="Optional source filter"
          />
          <button
            type="button"
            onClick={searchDocuments}
            disabled={busy !== null || !query.trim()}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {busy === "search" ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
            Search
          </button>
        </div>

        {results ? (
          <div className="mt-4 space-y-2">
            <p className="text-xs text-ink-faint">
              {results.hits.length} results · {results.model_version}
            </p>
            {results.hits.length === 0 ? (
              <p className="rounded-md border border-line px-3 py-5 text-center text-sm text-ink-faint">
                No indexed documents matched.
              </p>
            ) : (
              results.hits.map((hit) => (
                <article key={hit.document_uuid} className="rounded-md border border-line p-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-ink">{hit.title}</h3>
                    <span className="text-xs tabular-nums text-accent">{(hit.score * 100).toFixed(1)}%</span>
                  </div>
                  <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-ink-muted" dir="auto">
                    {hit.text}
                  </p>
                  <p className="mt-2 text-xs text-ink-faint">{hit.source} · {hit.primary_script}</p>
                </article>
              ))
            )}
          </div>
        ) : null}

        {error ? (
          <p className="mt-4 rounded-md border border-reject/30 bg-reject/10 px-3 py-2 text-sm text-reject">
            {error}
          </p>
        ) : null}
      </div>
    </section>
  );
}
