"use client";

import { useCallback, useEffect, useState } from "react";

type Analysis = { text?: string; model?: string; seconds?: number; segments?: { start: number; end: number; text: string }[] };
type Result = { analysis?: Analysis; frames?: (Analysis & { timestamp: number })[]; translation?: Analysis; transliteration?: Analysis; elapsed_seconds?: number; scope?: string; sha256?: string };
type Job = { id: string; kind: string; filename: string; state: string; error?: string; result?: Result; created: string };

export function MediaDemo() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState("video");
  const [language, setLanguage] = useState("en");
  const [script, setScript] = useState("original");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);
  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/demo", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Unable to load jobs.");
      setJobs(data.jobs); setError("");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to connect."); }
  }, []);
  useEffect(() => { const initial = setTimeout(() => { void refresh(); }, 0); const timer = setInterval(() => { void refresh(); }, 3000); return () => { clearTimeout(initial); clearInterval(timer); }; }, [refresh]);
  async function submit(sample: boolean) {
    if (!sample && !file) { setError("Choose a file first."); return; }
    if (!sample && file && file.size > 20 * 1024 * 1024) { setError("Choose a file below 20 MB."); return; }
    setBusy(true); setError("");
    const form = new FormData();
    form.set("kind", kind); form.set("language", sample ? "en" : language); form.set("script", script); form.set("sample", String(sample));
    if (!sample && file) form.set("file", file);
    try {
      const response = await fetch("/api/demo", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the file and selected options.");
      await refresh();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Submission failed."); }
    finally { setBusy(false); }
  }
  const field = "rounded border border-line bg-surface-sunken px-3 py-2 text-sm";
  return <section className="panel my-4 p-4" aria-labelledby="demo-title">
    <h2 id="demo-title" className="font-semibold">Media demonstration</h2>
    <p className="my-2 text-sm text-ink-muted">Upload a short clip or image to examine visible content, readable text and speech. Video uses three timestamped frames from the first minute. Maximum file size: 20 MB.</p>
    <div className="flex flex-wrap gap-3">
      <label>Analysis<select className={`${field} block`} value={kind} onChange={e => setKind(e.target.value)}><option value="video">Video</option><option value="image">Image</option><option value="audio">Audio</option><option value="ocr">Thaana text crop</option></select></label>
      <label>Speech language<select className={`${field} block`} value={language} onChange={e => setLanguage(e.target.value)}><option value="en">English</option><option value="dv">Dhivehi</option></select></label>
      <label>Output<select className={`${field} block`} value={script} onChange={e => setScript(e.target.value)}><option value="original">Original analysis</option><option value="thaana">Thaana translation (review needed)</option><option value="latin">Latin Dhivehi (experimental)</option></select></label>
    </div>
    <p className="my-2 text-xs text-ink-muted">Mixed-language speech and landmark names need review. The demo describes visible people; it does not identify them. Latin Dhivehi output is not accuracy-validated.</p>
    <input className="my-3 block" type="file" accept="image/png,image/jpeg,image/webp,audio/*,video/mp4,video/webm,video/quicktime" onChange={e => { const selected = e.target.files?.[0] ?? null; setFile(selected); setPreview(selected ? URL.createObjectURL(selected) : ""); }} />
    {file && preview ? <div className="mb-3 max-w-xl">{file.type.startsWith("video/") ? <video src={preview} controls className="max-h-64" /> : file.type.startsWith("audio/") ? <audio src={preview} controls /> : <a href={preview} target="_blank" rel="noreferrer">Preview selected image</a>}</div> : null}
    <div className="flex gap-3"><button className={field} disabled={busy} onClick={() => void submit(false)}>{busy ? "Submitting…" : "Analyze uploaded file"}</button><button className={field} disabled={busy || kind === "ocr"} onClick={() => void submit(true)}>Run labeled synthetic sample</button></div>
    {error ? <p role="alert" className="my-3 text-reject">{error}</p> : null}
    <div aria-live="polite" className="mt-4 space-y-3">{jobs.map(job => <article key={job.id} className="rounded border border-line p-3">
      <h3 className="font-medium">{job.filename} · {job.kind} · {job.state}</h3>
      {job.state === "queued" || job.state === "running" ? <p className="text-sm">{job.state === "queued" ? "Waiting for the previous job." : "Analyzing media; this can take several minutes."}</p> : null}
      {job.error ? <p className="text-reject">{job.error}</p> : null}
      {job.result ? <>
        {job.result.analysis?.text ? <p dir="auto" className="my-2 whitespace-pre-wrap">{job.result.analysis.text}</p> : null}
        {job.result.analysis?.segments?.map((segment, i) => <p key={i} dir="auto" className="text-sm">{segment.start.toFixed(1)}–{segment.end.toFixed(1)}s: {segment.text}</p>)}
        {job.result.frames?.map(frame => <p key={frame.timestamp} className="my-2"><strong>{frame.timestamp.toFixed(1)}s:</strong> {frame.text}</p>)}
        {job.result.translation ? <p dir="auto" className="my-2">Translation: {job.result.translation.text}</p> : null}
        {job.result.transliteration ? <p dir="auto" className="my-2">Latin Dhivehi: {job.result.transliteration.text}</p> : null}
        <p className="text-xs text-ink-muted">Completed in {job.result.elapsed_seconds}s · {job.result.scope}</p>
        <details className="mt-2"><summary>Source hash and model provenance</summary><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(job.result, null, 2)}</pre></details>
      </> : null}
    </article>)}</div>
  </section>;
}
