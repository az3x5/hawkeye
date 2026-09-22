"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
  created_at: string; updated_at: string;
  processing?: {
    status: string; attempt_count: number; max_attempts: number; queued_at: string;
    started_at: string | null; completed_at: string | null; available_at: string;
    worker_id: string | null; error_code: string | null; error_detail: string | null;
  } | null;
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
type ReportSummary = {
  analysis_id: string; report_request_id: string | null; subject: { display_label?: string } | null;
  source_id: string; source_type: string; owner: string; status: string; revision: number;
  created_at: string; updated_at: string; page_url: string;
};
type ReportPage = { items: ReportSummary[]; total: number; limit: number; offset: number };
const base = "/api/v1/integrations/blackglass/evidence";
const documentsBase = "/api/v1/integrations/blackglass/documents";
const field = "rounded border border-white/15 bg-black/10 p-3 text-sm";

async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(15000), ...init });
  const body = await response.json();
  if (!response.ok) throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  return body as T;
}

export function EvidenceWorkspace({ canText, canMedia, initialAnalysisId = "", reportOnly = false }: { canText: boolean; canMedia: boolean; initialAnalysisId?: string; reportOnly?: boolean }) {
  const router = useRouter();
  const [record, setRecord] = useState("");
  const [text, setText] = useState("");
  const [language, setLanguage] = useState("unknown");
  const [file, setFile] = useState<File | null>(null);
  const [active, setActive] = useState(initialAnalysisId);
  const [workflow, setWorkflow] = useState<WorkflowStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [lookup, setLookup] = useState(initialAnalysisId);
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
  const [reports, setReports] = useState<ReportPage | null>(null);
  const [reportsError, setReportsError] = useState("");

  useEffect(() => {
    if (reportOnly) return;
    let stopped = false;
    let timeout: ReturnType<typeof setTimeout>;
    async function pollReports() {
      try {
        const page = await readJson<ReportPage>(`${base}?limit=50`);
        if (!stopped) { setReports(page); setReportsError(""); }
      } catch (caught) {
        if (!stopped) setReportsError(caught instanceof Error ? caught.message : "Unable to load reports.");
      } finally {
        if (!stopped) timeout = setTimeout(pollReports, 5000);
      }
    }
    void pollReports();
    return () => { stopped = true; clearTimeout(timeout); };
  }, [reportOnly]);

  useEffect(() => {
    if (reportOnly) return;
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
  }, [reportOnly]);

  useEffect(() => {
    if (!canText || reportOnly) return;
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
  }, [appliedProfile, canText, documentOffset, reportOnly]);

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
  }, [active]);

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
      openReport(accepted.analysis_id);
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

  function openReport(analysisId: string) {
    const value = analysisId.trim();
    if (!value) return;
    setLookup(value); setActive(value); setResult(null);
    router.push(`/evidence/${encodeURIComponent(value)}`);
  }

  async function analyzeProfile() {
    if (!reportProfile) return setError("Filter to one profile before generating its report.");
    setProfileAnalysisBusy(true); setError(""); setResult(null);
    try {
      const accepted = await readJson<{ analysis_id: string }>(
        `/api/v1/integrations/blackglass/profiles/${encodeURIComponent(reportProfile)}/analyze`,
        { method: "POST" },
      );
      openReport(accepted.analysis_id);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Profile analysis could not be started."); }
    finally { setProfileAnalysisBusy(false); }
  }

  if (reportOnly) return <div className="space-y-6">
    <Link className="inline-block text-sm underline" href="/evidence">← All evidence reports</Link>
    {error && <p role="alert" className="panel p-4 text-red-400">{error}</p>}
    {result ? <ReportResult result={result} /> : <section className="panel p-6 text-sm text-ink-muted">Loading report…</section>}
  </div>;

  return <div className="space-y-6">
    <section className="panel space-y-4 p-6" aria-live="polite">
      <div><h2 className="text-lg font-semibold">BlackGlass report pages</h2>
        <p className="text-sm text-ink-muted">Every accepted evidence request has a permanent page. {reports ? `${reports.total.toLocaleString()} reports stored` : "Loading reports…"}</p></div>
      {reportsError && <p className="text-sm text-red-400">{reportsError}</p>}
      {reports?.items.length === 0 && <p className="text-sm text-ink-muted">No reports have been received yet.</p>}
      <div className="grid gap-3 md:grid-cols-2">{reports?.items.map(report => <Link key={report.analysis_id} href={report.page_url} className="rounded border border-white/10 p-4 transition hover:border-cyan-400/50">
        <div className="flex items-start justify-between gap-3"><h3 className="font-semibold" dir="auto">{report.subject?.display_label || report.source_id}</h3><span className="rounded-full border border-white/15 px-2 py-1 text-xs">{report.status}</span></div>
        <p className="mt-2 text-xs text-ink-muted">{report.source_type} · revision {report.revision} · {new Date(report.created_at).toLocaleString()}</p>
        <p className="mt-1 truncate text-xs text-ink-muted">{report.report_request_id || report.analysis_id}</p>
      </Link>)}</div>
    </section>
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
        <Button onClick={() => openReport(lookup)}>Open</Button></div>
      <label htmlFor="evidence-query">Search your evidence</label>
      <div className="flex gap-2"><input id="evidence-query" dir="auto" className={`${field} flex-1`} value={query} onChange={e => setQuery(e.target.value)} />
        <Button onClick={() => void search()}>Search</Button></div>
      {hits.map(hit => <button className="text-left text-sm underline" key={hit.evidence.evidence_id} onClick={() => openReport(hit.analysis_id)}><span dir="auto">{hit.evidence.original_text.slice(0, 200)}</span></button>)}
    </section>
    {error && <p role="alert" className="panel p-4 text-red-400">{error}</p>}
    {result && <ReportResult result={result} />}
  </div>;
}

function ReportResult({ result }: { result: Result }) {
  const findings = [...(result.findings ?? []), ...(result.contradictions ?? [])];
  const findingsBySection = findings.reduce<Record<string, Finding[]>>((groups, finding) => {
    (groups[finding.section] ??= []).push(finding);
    return groups;
  }, {});
  const executiveFindings = findings.slice(0, 5);
  const processing = result.processing;
  const isActive = ["queued", "running", "retry"].includes(processing?.status ?? result.status);
  const stateLabel = isActive
    ? processing?.status === "running" ? "Analysis is running" : processing?.status === "retry" ? "Analysis will retry" : "Analysis is queued"
    : result.status === "partial" ? "Analysis finished with gaps" : result.status === "completed" ? "Analysis finished" : "Analysis stopped";
  const stateDetail = isActive
    ? processing?.status === "running" ? "A worker is extracting evidence and generating cited findings. This page refreshes automatically." : "Waiting for an analysis worker. This page refreshes automatically."
    : result.status === "partial" ? `${findings.length} cited findings were retained; ${result.warnings?.length ?? 0} batches were rejected or could not be validated.` : `${findings.length} cited findings were retained from ${result.evidence.length} evidence sections.`;

  return <section className="panel space-y-5 p-6" aria-live="polite">
      <div className="rounded border border-white/10 bg-black/10 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-cyan-300">Processing status</p>
            <h2 className="mt-1 text-lg font-semibold">{stateLabel}</h2>
          </div>
          <span className={`rounded-full border px-3 py-1 text-xs font-medium ${result.status === "partial" ? "border-amber-400/40 text-amber-300" : isActive ? "border-cyan-400/40 text-cyan-300" : "border-emerald-400/40 text-emerald-300"}`}>{processing?.status ?? result.status}</span>
        </div>
        <p className="mt-2 text-sm text-ink-muted">{stateDetail}</p>
        <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-ink-faint">Source</dt><dd className="mt-1 break-all text-ink">{result.source_id}</dd></div>
          <div><dt className="text-ink-faint">Evidence sections</dt><dd className="mt-1 text-ink">{result.evidence.length.toLocaleString()}</dd></div>
          <div><dt className="text-ink-faint">Worker attempts</dt><dd className="mt-1 text-ink">{processing ? `${processing.attempt_count} of ${processing.max_attempts}` : "Not reported"}</dd></div>
          <div><dt className="text-ink-faint">Last update</dt><dd className="mt-1 text-ink">{new Date(result.updated_at).toLocaleString()}</dd></div>
        </dl>
        {processing?.error_code && <p className="mt-3 text-sm text-red-400">Worker error: {processing.error_code.replaceAll("_", " ")}{processing.error_detail ? ` — ${processing.error_detail}` : ""}</p>}
      </div>
      <p className="text-sm text-ink-muted">Findings are unreviewed model output. Citations verify referenced excerpts, not the truth of a conclusion.</p>
      <a className="underline" href={`${base}/${result.analysis_id}/content`}>Download original evidence</a>
      <a className="ml-4 underline" href={`${base}/${result.analysis_id}/report`} download>Download BlackGlass report JSON</a>
      <p className="break-all text-xs">SHA-256: {result.source_sha256} · {result.storage_mode}</p>
      {(result.warnings?.length ?? 0) > 0 && <details className="rounded border border-amber-400/20 bg-amber-400/5 p-4">
        <summary className="cursor-pointer text-sm font-medium text-amber-300">{result.warnings?.length} processing warnings — show details</summary>
        <div className="mt-3 space-y-2">{result.warnings?.map((warning, i) => <p key={i} className="text-sm text-amber-300">{warning.stage}: {warning.code.replaceAll("_", " ")}</p>)}</div>
      </details>}
      <section className="rounded border border-cyan-400/20 bg-cyan-400/5 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-cyan-300">Executive summary</p>
        <h3 className="mt-1 text-lg font-semibold">Evidence-backed analytical overview</h3>
        <p className="mt-2 text-sm text-ink-muted">
          This report retained {findings.length.toLocaleString()} citation-validated analytical {findings.length === 1 ? "finding" : "findings"} from {result.evidence.length.toLocaleString()} evidence sections.
          {result.status === "partial" ? ` ${result.warnings?.length ?? 0} processing batches did not produce valid cited output, so this summary is incomplete.` : " All displayed conclusions remain unreviewed."}
        </p>
        {executiveFindings.length > 0 ? <ol className="mt-4 space-y-3">
          {executiveFindings.map((finding, index) => <li key={`${finding.section}-${index}`} className="flex gap-3 text-sm">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-cyan-400/30 text-xs text-cyan-300">{index + 1}</span>
            <span dir="auto">{finding.statement}</span>
          </li>)}
        </ol> : !isActive && <p className="mt-4 text-sm text-amber-300">No conclusion passed citation validation; only the retained source evidence can be reviewed.</p>}
      </section>
      <div className="border-b border-white/10 pb-2"><h3 className="text-base font-semibold">Analysis by section</h3><p className="text-sm text-ink-muted">Only claims that passed citation checks appear below. Missing sections do not imply a negative finding.</p></div>
      {findings.length === 0 && !isActive && <p className="rounded border border-amber-400/20 p-4 text-sm text-amber-300">No model findings passed citation validation. The original evidence is preserved below.</p>}
      {Object.entries(findingsBySection).map(([section, sectionFindings]) => <section key={section} className="rounded border border-white/10 p-4">
        <h4 className="mb-4 text-sm font-semibold uppercase tracking-wide text-cyan-300">{section.replace(/^osp-/, "").replaceAll("-", " ")}</h4>
        <div className="space-y-4">{sectionFindings.map((finding, i) => <article key={i} className="border-l-2 border-cyan-500 pl-4">
          <p dir="auto">{finding.statement}</p>
          <div className="mt-2 space-y-1">{finding.citations.map((citation, j) => <a className="block text-sm underline" key={j} href={`#evidence-${citation.evidence_id}`}><span dir="auto">“{citation.quote}”</span></a>)}</div>
        </article>)}</div>
      </section>)}
      <details className="border-t border-white/10 pt-4">
        <summary className="cursor-pointer text-base font-semibold">Source evidence ({result.evidence.length.toLocaleString()} sections)</summary>
        <div className="mt-4 space-y-5">{result.evidence.map(piece => <article id={`evidence-${piece.evidence_id}`} key={piece.evidence_id} className="scroll-mt-24 space-y-2 border-t border-white/10 pt-4">
        <h3 className="font-medium">{piece.kind.replaceAll("_", " ")}</h3>
        <p className="text-xs text-ink-muted">{piece.locator.page != null && `Page ${piece.locator.page} · `}{piece.locator.start_ms != null && `${piece.locator.start_ms / 1000}–${(piece.locator.end_ms ?? 0) / 1000}s · `}{piece.locator.precision.replaceAll("_", " ")}</p>
        <p dir="auto" className="whitespace-pre-wrap leading-8">{piece.original_text}</p>
        {piece.translations.map((translation, i) => <div key={i}><span className="text-xs">{translation.task}</span><p dir="auto" className="leading-8">{translation.text}</p></div>)}
        <details className="text-xs"><summary>Processing provenance</summary><pre className="overflow-x-auto">{JSON.stringify(piece.provenance, null, 2)}</pre></details>
        </article>)}</div>
      </details>
    </section>;
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
