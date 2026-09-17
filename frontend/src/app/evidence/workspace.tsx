"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type Piece = {
  evidence_id: string; kind: string; original_text: string; normalized_text: string;
  locator: { page?: number; start_ms?: number; end_ms?: number; char_start?: number; char_end?: number; precision: string };
  translations: Array<{ task: string; text: string }>;
  provenance: Record<string, string>;
};
type Finding = { statement: string; section: string; citations: Array<{ evidence_id: string; quote: string }> };
type Result = {
  analysis_id: string; status: string; revision: number; storage_mode: string;
  source_id: string; source_type: string; attributes: Record<string, unknown>; source_sha256: string;
  evidence: Piece[]; findings?: Finding[]; contradictions?: Finding[];
  warnings?: Array<{ stage: string; code: string }>;
};
type WorkflowStatus = {
  measured_at: string; runs: Record<string, number>; analysis_workers: number;
  delivery_workers: number; undelivered_events: number;
};
type ImportedDocument = {
  document_uuid: string; source_id: string; source_type: string; profile_id: string | null;
  title: string; source: string; primary_script: string; processing_state: string;
  attributes: Record<string, unknown>; created_at: string; processed_at: string | null;
};
type ImportedDocumentPage = {
  items: ImportedDocument[]; total: number; limit: number; offset: number; profiles: Record<string, number>;
};
const base = "/api/v1/integrations/blackglass/evidence";
const documentsBase = "/api/v1/integrations/blackglass/documents";
const field = "rounded border border-white/15 bg-black/10 p-3 text-sm";

async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(15000), ...init });
  const body = await response.json();
  if (!response.ok) throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  return body as T;
}

export function EvidenceWorkspace({ canText, canMedia }: { canText: boolean; canMedia: boolean }) {
  const [record, setRecord] = useState("");
  const [text, setText] = useState("");
  const [language, setLanguage] = useState("unknown");
  const [file, setFile] = useState<File | null>(null);
  const [active, setActive] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [workflow, setWorkflow] = useState<WorkflowStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [lookup, setLookup] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Array<{ analysis_id: string; evidence: Piece }>>([]);
  const [documents, setDocuments] = useState<ImportedDocumentPage | null>(null);
  const [documentsError, setDocumentsError] = useState("");
  const [profileFilter, setProfileFilter] = useState("");
  const [appliedProfile, setAppliedProfile] = useState("");
  const [documentOffset, setDocumentOffset] = useState(0);
  const [profileAnalysisBusy, setProfileAnalysisBusy] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timeout: ReturnType<typeof setTimeout>;
    async function pollStatus() {
      try {
        const value = await readJson<WorkflowStatus>(`${base}/status`);
        if (!stopped) { setWorkflow(value); setStatusError(""); }
      } catch {
        if (!stopped) { setWorkflow(null); setStatusError("Worker status unavailable; no readiness claim can be made."); }
      } finally {
        if (!stopped) timeout = setTimeout(pollStatus, 10000);
      }
    }
    void pollStatus();
    return () => { stopped = true; clearTimeout(timeout); };
  }, []);

  useEffect(() => {
    if (!canText) return;
    let stopped = false;
    let timeout: ReturnType<typeof setTimeout>;
    async function pollDocuments() {
      const parameters = new URLSearchParams({ limit: "25", offset: String(documentOffset) });
      if (appliedProfile) parameters.set("profile_id", appliedProfile);
      try {
        const page = await readJson<ImportedDocumentPage>(`${documentsBase}?${parameters}`);
        if (!stopped) { setDocuments(page); setDocumentsError(""); }
      } catch (caught) {
        if (!stopped) setDocumentsError(caught instanceof Error ? caught.message : "Unable to load imported records.");
      } finally {
        if (!stopped) timeout = setTimeout(pollDocuments, 5000);
      }
    }
    void pollDocuments();
    return () => { stopped = true; clearTimeout(timeout); };
  }, [appliedProfile, canText, documentOffset]);

  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timeout: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function poll() {
      try {
        const value = await readJson<Result>(`${base}/${encodeURIComponent(active)}`);
        if (stopped) return;
        setResult(value);
        setError("");
        failures = 0;
        if (["queued", "running", "retry"].includes(value.status)) timeout = setTimeout(poll, 3000);
      } catch (caught) {
        if (stopped) return;
        setError(caught instanceof Error ? caught.message : "Unable to read analysis.");
        if (++failures < 5) timeout = setTimeout(poll, 10000);
      }
    }
    void poll();
    return () => { stopped = true; clearTimeout(timeout); };
  }, [active, refresh]);

  async function submit(useFile: boolean) {
    if (!record.trim()) return setError("Enter the original BlackGlass record ID.");
    if (useFile && (!file || file.size > 50 * 1024 * 1024)) return setError("Choose a file under 50 MiB.");
    if (!useFile && !text.trim()) return setError("Enter source text.");
    setBusy(true); setError(""); setResult(null);
    const metadata = {
      schema_version: "1.1",
      source_id: record.trim(),
      source_type: useFile ? "media" : "text",
      attributes: { source_system: "blackglass-prod" },
      options: { language },
    };
    try {
      let init: RequestInit;
      if (useFile && file) {
        const form = new FormData(); form.set("metadata", JSON.stringify(metadata)); form.set("file", file);
        init = { method: "POST", body: form };
      } else init = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...metadata, text }) };
      const accepted = await readJson<{ analysis_id: string }>(`${base}/${useFile ? "media" : "text"}`, init);
      setActive(accepted.analysis_id); setLookup(accepted.analysis_id);
      setRefresh(value => value + 1);
      setResult(await readJson<Result>(`${base}/${accepted.analysis_id}`));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Submission failed."); }
    finally { setBusy(false); }
  }

  async function search() {
    if (!query.trim()) return;
    try {
      const response = await readJson<{ items: typeof hits }>(`/api/v1/integrations/blackglass/evidence-search?q=${encodeURIComponent(query)}`);
      setHits(response.items); setError("");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Search failed."); }
  }

  const profileIds = Object.keys(documents?.profiles ?? {});
  const reportProfile = appliedProfile || (profileIds.length === 1 ? profileIds[0] : "");

  async function analyzeProfile() {
    if (!reportProfile) return setError("Filter to one profile before generating its report.");
    setProfileAnalysisBusy(true); setError(""); setResult(null);
    try {
      const accepted = await readJson<{ analysis_id: string }>(
        `/api/v1/integrations/blackglass/profiles/${encodeURIComponent(reportProfile)}/analyze`,
        { method: "POST" },
      );
      setActive(accepted.analysis_id); setLookup(accepted.analysis_id);
      setRefresh(value => value + 1);
      setResult(await readJson<Result>(`${base}/${accepted.analysis_id}`));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Profile analysis could not be started."); }
    finally { setProfileAnalysisBusy(false); }
  }

  return <div className="space-y-6">
    {canText && <section className="panel space-y-4 p-6" aria-live="polite">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="text-lg font-semibold">Imported BlackGlass records</h2>
          <p className="text-sm text-ink-muted">{documents ? `${documents.total.toLocaleString()} records · refreshes every 5 seconds` : "Loading imported records…"}</p></div>
        <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); setDocumentOffset(0); setAppliedProfile(profileFilter.trim()); }}>
          <input className={field} aria-label="Filter by profile ID" placeholder="Profile ID" value={profileFilter} onChange={event => setProfileFilter(event.target.value)} maxLength={256} />
          <Button type="submit">Filter</Button>
          {appliedProfile && <Button type="button" variant="secondary" onClick={() => { setProfileFilter(""); setAppliedProfile(""); setDocumentOffset(0); }}>Clear</Button>}
        </form>
      </div>
      {documentsError && <p className="text-sm text-red-400">{documentsError}</p>}
      {canMedia && reportProfile && <div className="flex flex-wrap items-center gap-3 rounded border border-white/10 p-3">
        <Button disabled={profileAnalysisBusy} onClick={() => void analyzeProfile()}>{profileAnalysisBusy ? "Starting report…" : "Generate profile report"}</Button>
        <span className="text-xs text-ink-muted">Uses all {documents?.profiles[reportProfile] ?? 0} imported records for profile {reportProfile}.</span>
      </div>}
      {documents?.items.length === 0 && <p className="text-sm text-ink-muted">No records match this filter.</p>}
      {documents && documents.items.length > 0 && <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm">
        <thead className="border-b border-white/15 text-xs text-ink-muted"><tr><th className="py-2 pr-3">Record</th><th className="py-2 pr-3">Profile</th><th className="py-2 pr-3">Type</th><th className="py-2 pr-3">State</th><th className="py-2">Received</th></tr></thead>
        <tbody className="divide-y divide-white/10">{documents.items.map(document => {
          const sourceUrl = safeHttpUrl(document.attributes.source_url);
          return <tr key={document.document_uuid}>
            <td className="py-3 pr-3"><p className="font-medium" dir="auto">{document.title}</p><p className="max-w-[22rem] truncate text-xs text-ink-muted" title={document.source_id}>{sourceUrl ? <a className="underline" href={sourceUrl} target="_blank" rel="noreferrer">{document.source_id}</a> : document.source_id}</p></td>
            <td className="py-3 pr-3 text-xs">{document.profile_id ?? "—"}</td>
            <td className="py-3 pr-3">{document.source_type}</td>
            <td className="py-3 pr-3"><span className="rounded-full border border-white/15 px-2 py-1 text-xs">{document.processing_state}</span></td>
            <td className="py-3 text-xs text-ink-muted">{new Date(document.created_at).toLocaleString()}</td>
          </tr>;
        })}</tbody>
      </table></div>}
      {documents && documents.total > documents.limit && <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" disabled={documentOffset === 0} onClick={() => setDocumentOffset(Math.max(0, documentOffset - documents.limit))}>Previous</Button>
        <span className="text-xs text-ink-muted">{documentOffset + 1}–{Math.min(documentOffset + documents.limit, documents.total)} of {documents.total}</span>
        <Button variant="secondary" disabled={documentOffset + documents.limit >= documents.total} onClick={() => setDocumentOffset(documentOffset + documents.limit)}>Next</Button>
      </div>}
    </section>}
    <section className="panel space-y-2 p-6" aria-live="polite">
      <h2 className="text-lg font-semibold">Processing workers</h2>
      {workflow ? <>
        <p>Analysis: {workflow.analysis_workers} online · Results delivery: {workflow.delivery_workers} online</p>
        <p className="text-sm">Your queued/retry analyses: {(workflow.runs.queued ?? 0) + (workflow.runs.retry ?? 0)} · Running: {workflow.runs.running ?? 0} · Undelivered events: {workflow.undelivered_events}</p>
        {workflow.analysis_workers === 0 && <p className="text-amber-400">No recent analysis-worker heartbeat. New submissions may remain queued.</p>}
        <p className="text-xs text-ink-muted">Updated {new Date(workflow.measured_at).toLocaleTimeString()}. Worker presence does not establish model accuracy.</p>
      </> : <p className="text-sm text-amber-400">{statusError || "Checking workers…"}</p>}
    </section>
    <section className="panel grid gap-4 p-6">
      <h2 className="text-lg font-semibold">Submit evidence</h2>
      <p className="text-sm text-ink-muted">For bulk AWS data, use the shared-object API to retain one original. Files uploaded here are stored by EagleEye.</p>
      <input className={field} aria-label="BlackGlass record ID" placeholder="BlackGlass record ID" value={record} onChange={e => setRecord(e.target.value)} maxLength={256} />
      <label className="text-sm">Source language <select className={field} value={language} onChange={e => setLanguage(e.target.value)}>
        <option value="unknown">Unknown</option><option value="dv">Dhivehi</option><option value="en">English</option><option value="mixed">Mixed</option>
      </select></label>
      {canText && <><textarea className={field} dir="auto" rows={5} maxLength={100000} aria-label="Original source text" placeholder="Original Thaana, Latin Dhivehi or English text" value={text} onChange={e => setText(e.target.value)} />
        <Button disabled={busy} onClick={() => void submit(false)}>Analyze text</Button></>}
      {canMedia && <div className="flex flex-wrap gap-3"><input type="file" aria-label="Evidence file" onChange={e => setFile(e.target.files?.[0] ?? null)} />
        <Button disabled={busy} onClick={() => void submit(true)}>Analyze file</Button></div>}
    </section>
    <section className="panel grid gap-3 p-6">
      <label htmlFor="analysis-id">Open an analysis</label>
      <div className="flex gap-2"><input id="analysis-id" className={`${field} flex-1`} value={lookup} onChange={e => setLookup(e.target.value)} placeholder="Analysis UUID" />
        <Button onClick={() => { setResult(null); setActive(lookup.trim()); setRefresh(value => value + 1); }}>Open</Button></div>
      <label htmlFor="evidence-query">Search your evidence</label>
      <div className="flex gap-2"><input id="evidence-query" dir="auto" className={`${field} flex-1`} value={query} onChange={e => setQuery(e.target.value)} />
        <Button onClick={() => void search()}>Search</Button></div>
      {hits.map(hit => <button className="text-left text-sm underline" key={hit.evidence.evidence_id} onClick={() => { setLookup(hit.analysis_id); setActive(hit.analysis_id); }}><span dir="auto">{hit.evidence.original_text.slice(0, 200)}</span></button>)}
    </section>
    {error && <p role="alert" className="panel p-4 text-red-400">{error}</p>}
    {result && <section className="panel space-y-5 p-6" aria-live="polite">
      <h2 className="text-lg font-semibold">{result.source_id} · {result.status} · revision {result.revision}</h2>
      <p className="text-sm text-ink-muted">Findings are unreviewed model output. Citations verify referenced excerpts, not the truth of a conclusion.</p>
      <a className="underline" href={`${base}/${result.analysis_id}/content`}>Download original evidence</a>
      <a className="ml-4 underline" href={`${base}/${result.analysis_id}/report`} download>Download BlackGlass report JSON</a>
      <p className="break-all text-xs">SHA-256: {result.source_sha256} · {result.storage_mode}</p>
      {result.warnings?.map((warning, i) => <p key={i} className="text-sm text-amber-400">{warning.stage}: {warning.code.replaceAll("_", " ")}</p>)}
      {[...(result.findings ?? []), ...(result.contradictions ?? [])].map((finding, i) => <article key={i} className="border-l-2 border-cyan-500 pl-4">
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-cyan-300">{finding.section.replace(/^osp-/, "").replaceAll("-", " ")}</p>
        <p dir="auto">{finding.statement}</p>
        {finding.citations.map((citation, j) => <a className="block text-sm underline" key={j} href={`#evidence-${citation.evidence_id}`}><span dir="auto">“{citation.quote}”</span></a>)}
      </article>)}
      {result.evidence.map(piece => <article id={`evidence-${piece.evidence_id}`} key={piece.evidence_id} className="scroll-mt-24 space-y-2 border-t border-white/10 pt-4">
        <h3 className="font-medium">{piece.kind.replaceAll("_", " ")}</h3>
        <p className="text-xs text-ink-muted">{piece.locator.page != null && `Page ${piece.locator.page} · `}{piece.locator.start_ms != null && `${piece.locator.start_ms / 1000}–${(piece.locator.end_ms ?? 0) / 1000}s · `}{piece.locator.precision.replaceAll("_", " ")}</p>
        <p dir="auto" className="whitespace-pre-wrap leading-8">{piece.original_text}</p>
        {piece.translations.map((translation, i) => <div key={i}><span className="text-xs">{translation.task}</span><p dir="auto" className="leading-8">{translation.text}</p></div>)}
        <details className="text-xs"><summary>Processing provenance</summary><pre className="overflow-x-auto">{JSON.stringify(piece.provenance, null, 2)}</pre></details>
      </article>)}
    </section>}
  </div>;
}

function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}
