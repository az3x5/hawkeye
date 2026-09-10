"use client";

import {
  AudioLines,
  BrainCircuit,
  FileImage,
  Film,
  RefreshCw,
  ScanSearch,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { StatusBadge, type StatusTone } from "@/components/states/status-badge";
import { Button } from "@/components/ui/button";

type Analysis = {
  text?: string;
  model?: string;
  revision?: string;
  seconds?: number;
  segments?: { start: number; end: number; text: string }[];
};
type Result = {
  analysis?: Analysis;
  frames?: (Analysis & { timestamp: number })[];
  translation?: Analysis;
  transliteration?: Analysis;
  elapsed_seconds?: number;
  scope?: string;
  sha256?: string;
};
type Job = {
  id: string;
  kind: string;
  filename: string;
  state: string;
  error?: string;
  result?: Result;
  created: string;
};
type ServiceModel = {
  task: string;
  model: string;
  revision?: string | null;
  state: "ready" | "missing" | "unavailable" | "error";
};
type Service = {
  status: "ready" | "degraded";
  models: ServiceModel[];
  limits: { max_upload_mb: number; video_window_seconds: number; video_sampled_frames: number };
  tracking: { installed: boolean; connected: boolean };
  checked_at: number;
};

const KINDS = [
  { value: "video", label: "Video", detail: "Sample frames and speech", icon: Film },
  { value: "image", label: "Image", detail: "Objects, scene and text", icon: FileImage },
  { value: "audio", label: "Audio", detail: "English or Dhivehi speech", icon: AudioLines },
  { value: "ocr", label: "Thaana OCR", detail: "Focused text crop", icon: ScanSearch },
] as const;

function modelLabel(task: string) {
  return task.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
}

function jobTone(state: string): StatusTone {
  if (state === "completed") return "accept";
  if (state === "failed") return "reject";
  if (state === "running" || state === "queued") return "review";
  return "neutral";
}

export function MediaWorkspace() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [service, setService] = useState<Service | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState("video");
  const [language, setLanguage] = useState("en");
  const [script, setScript] = useState("original");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/demo", { cache: "no-store" });
      const data = (await response.json()) as {
        detail?: string;
        jobs?: Job[];
        service?: Service;
      };
      if (!response.ok) throw new Error(data.detail ?? "Unable to load vision jobs.");
      setJobs(data.jobs ?? []);
      setService(data.service ?? null);
      setError("");
    } catch (caught) {
      setService(null);
      setError(caught instanceof Error ? caught.message : "Unable to connect to vision service.");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 3_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const queue = useMemo(
    () => jobs.filter((job) => job.state === "queued" || job.state === "running").length,
    [jobs],
  );

  function selectFile(selected: File | null) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(selected);
    setPreview(selected ? URL.createObjectURL(selected) : "");
  }

  async function submit(sample: boolean) {
    if (!sample && !file) {
      setError("Choose a file first.");
      return;
    }
    if (!sample && file && file.size > 20 * 1024 * 1024) {
      setError("Choose a file below 20 MB.");
      return;
    }
    setBusy(true);
    setError("");
    const form = new FormData();
    form.set("kind", kind);
    form.set("language", sample ? "en" : language);
    form.set("script", script);
    form.set("sample", String(sample));
    if (!sample && file) form.set("file", file);
    try {
      const response = await fetch("/api/demo", { method: "POST", body: form });
      const data = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(data.detail ?? "Check the file and selected options.");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Submission failed.");
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1.5 w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink outline-none focus:border-accent";

  return (
    <div className="space-y-5">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Vision service status">
        <StatusCard
          label="Inference service"
          value={service?.status === "ready" ? "Online" : service ? "Degraded" : "Connecting"}
          tone={service?.status === "ready" ? "accept" : service ? "review" : "neutral"}
          detail={service ? `${service.models.filter((model) => model.state === "ready").length} models ready` : "Waiting for a live response"}
        />
        <StatusCard label="Active queue" value={String(queue)} tone={queue > 0 ? "review" : "accept"} detail="Serialized evidence jobs" />
        <StatusCard
          label="Object detection"
          value={service?.tracking.installed ? "Installed" : service ? "Not installed" : "Checking"}
          tone={service?.tracking.installed ? "accept" : "neutral"}
          detail="RT-DETR specialist artifacts"
        />
        <StatusCard
          label="Continuous tracking"
          value={service?.tracking.connected ? "Connected" : "Not connected"}
          tone={service?.tracking.connected ? "accept" : "review"}
          detail="No identity claim from tracks"
        />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(20rem,0.65fr)]">
        <section className="panel overflow-hidden" aria-labelledby="analyze-title">
          <div className="border-b border-line px-5 py-4">
            <div className="flex items-center gap-3">
              <span className="rounded-md bg-accent/10 p-2 text-accent"><BrainCircuit className="size-5" /></span>
              <div>
                <h2 id="analyze-title" className="font-semibold text-ink">New analysis</h2>
                <p className="text-sm text-ink-muted">Select the evidence type and processing output.</p>
              </div>
            </div>
          </div>

          <div className="space-y-5 p-5">
            <fieldset>
              <legend className="mb-2 text-xs font-semibold tracking-wide text-ink-muted uppercase">Evidence type</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {KINDS.map((option) => {
                  const active = kind === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setKind(option.value)}
                      className={`flex items-center gap-3 rounded-md border p-3 text-left transition-colors ${active ? "border-accent bg-accent/10" : "border-line bg-surface-sunken hover:border-line-strong"}`}
                    >
                      <option.icon className={`size-5 shrink-0 ${active ? "text-accent" : "text-ink-faint"}`} />
                      <span><span className="block text-sm font-semibold text-ink">{option.label}</span><span className="block text-xs text-ink-muted">{option.detail}</span></span>
                    </button>
                  );
                })}
              </div>
            </fieldset>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm text-ink-muted">Speech language
                <select className={field} value={language} onChange={(event) => setLanguage(event.target.value)} disabled={kind === "image" || kind === "ocr"}>
                  <option value="en">English</option><option value="dv">Dhivehi</option>
                </select>
              </label>
              <label className="text-sm text-ink-muted">Output script
                <select className={field} value={script} onChange={(event) => setScript(event.target.value)}>
                  <option value="original">Original analysis</option><option value="thaana">Thaana translation — review needed</option><option value="latin">Latin Dhivehi — experimental</option>
                </select>
              </label>
            </div>

            <label className="group flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-surface-sunken px-5 py-6 text-center transition-colors hover:border-accent/70 hover:bg-accent/5">
              <Upload className="mb-2 size-6 text-accent" />
              <span className="text-sm font-semibold text-ink">{file ? file.name : "Choose evidence file"}</span>
              <span className="mt-1 text-xs text-ink-muted">PNG, JPEG, WebP, MP4, WebM, MOV or audio · maximum 20 MB</span>
              <input className="sr-only" type="file" accept="image/png,image/jpeg,image/webp,audio/*,video/mp4,video/webm,video/quicktime" onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
            </label>

            {file && preview ? (
              <div className="rounded-md border border-line bg-bg p-3 text-sm">
                {file.type.startsWith("video/") ? <video src={preview} controls className="max-h-72 w-full rounded object-contain" /> : file.type.startsWith("audio/") ? <audio src={preview} controls className="w-full" /> : <a href={preview} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-4">Open selected image preview</a>}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={busy} onClick={() => void submit(false)}><Upload />{busy ? "Submitting…" : "Analyze file"}</Button>
              <Button type="button" variant="outline" disabled={busy || kind === "ocr"} onClick={() => void submit(true)}>Run synthetic sample</Button>
            </div>
            <p className="text-xs leading-relaxed text-ink-faint">Descriptions are model observations, not verified facts. The current video path samples three frames from the first minute and can miss events. It does not identify visible people.</p>
          </div>
        </section>

        <ModelPanel service={service} refresh={refresh} />
      </div>

      {error ? <p role="alert" className="rounded-md border border-reject/30 bg-reject/10 px-4 py-3 text-sm text-reject">{error}</p> : null}

      <section aria-labelledby="jobs-title">
        <div className="mb-3 flex items-center justify-between">
          <div><h2 id="jobs-title" className="font-semibold text-ink">Recent analyses</h2><p className="text-sm text-ink-muted">Live updates every three seconds.</p></div>
          <Button type="button" variant="ghost" size="sm" onClick={() => void refresh()}><RefreshCw />Refresh</Button>
        </div>
        <div aria-live="polite" className="space-y-3">
          {jobs.length === 0 ? <div className="panel px-6 py-10 text-center text-sm text-ink-muted">No evidence has been analyzed by this account yet.</div> : jobs.map((job) => <JobCard key={job.id} job={job} />)}
        </div>
      </section>
    </div>
  );
}

function StatusCard({ label, value, tone, detail }: { label: string; value: string; tone: StatusTone; detail: string }) {
  return <div className="panel p-4"><p className="text-xs font-semibold tracking-wide text-ink-faint uppercase">{label}</p><div className="mt-2"><StatusBadge tone={tone}>{value}</StatusBadge></div><p className="mt-2 text-xs text-ink-muted">{detail}</p></div>;
}

function ModelPanel({ service, refresh }: { service: Service | null; refresh: () => Promise<void> }) {
  return <aside className="panel h-fit overflow-hidden" aria-labelledby="models-title">
    <div className="flex items-center justify-between border-b border-line px-4 py-3"><div><h2 id="models-title" className="text-sm font-semibold text-ink">Runtime models</h2><p className="text-xs text-ink-muted">Reported by the inference service</p></div><Button type="button" variant="ghost" size="icon-sm" aria-label="Refresh model status" onClick={() => void refresh()}><RefreshCw /></Button></div>
    <div className="divide-y divide-line">
      {service?.models.length ? service.models.map((model) => <div key={`${model.task}-${model.model}`} className="px-4 py-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="text-xs text-ink-faint">{modelLabel(model.task)}</p><p className="mt-0.5 truncate text-sm font-medium text-ink" title={model.model}>{model.model}</p></div><StatusBadge tone={model.state === "ready" ? "accept" : model.state === "missing" ? "reject" : "review"}>{model.state}</StatusBadge></div>{model.revision ? <p className="mt-1 truncate font-mono text-[0.65rem] text-ink-faint" title={model.revision}>{model.revision}</p> : null}</div>) : <p className="px-4 py-8 text-center text-sm text-ink-muted">Waiting for runtime inventory…</p>}
    </div>
  </aside>;
}

function JobCard({ job }: { job: Job }) {
  return <article className="panel p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-medium text-ink">{job.filename}</h3><p className="mt-0.5 text-xs text-ink-muted">{job.kind} · {new Date(`${job.created}Z`).toLocaleString()}</p></div><StatusBadge tone={jobTone(job.state)}>{job.state}</StatusBadge></div>
    {job.state === "queued" || job.state === "running" ? <p className="mt-3 text-sm text-ink-muted">{job.state === "queued" ? "Waiting for the previous analysis." : "Analyzing evidence; model inference can take several minutes."}</p> : null}
    {job.error ? <p className="mt-3 text-sm text-reject">{job.error}</p> : null}
    {job.result ? <div className="mt-4 space-y-3 border-t border-line pt-4">
      {job.result.analysis?.text ? <p dir="auto" className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{job.result.analysis.text}</p> : null}
      {job.result.analysis?.segments?.map((segment, index) => <p key={index} dir="auto" className="rounded bg-surface-sunken px-3 py-2 text-sm"><span className="mr-2 font-mono text-xs text-accent">{segment.start.toFixed(1)}–{segment.end.toFixed(1)}s</span>{segment.text}</p>)}
      {job.result.frames?.map((frame) => <p key={frame.timestamp} className="text-sm leading-relaxed"><strong className="mr-2 font-mono text-xs text-accent">{frame.timestamp.toFixed(1)}s</strong>{frame.text}</p>)}
      {job.result.translation?.text ? <div className="rounded-md border-l-2 border-accent bg-accent/5 px-3 py-2"><p className="text-xs font-semibold text-ink-muted">Translation</p><p dir="auto" className="mt-1 text-sm">{job.result.translation.text}</p></div> : null}
      {job.result.transliteration?.text ? <div className="rounded-md border-l-2 border-review bg-review/5 px-3 py-2"><p className="text-xs font-semibold text-ink-muted">Experimental Latin Dhivehi</p><p dir="auto" className="mt-1 text-sm">{job.result.transliteration.text}</p></div> : null}
      <p className="text-xs text-ink-faint">Completed in {job.result.elapsed_seconds ?? "—"}s · {job.result.scope}</p>
      <details><summary className="cursor-pointer text-xs text-ink-muted">Evidence hash and model provenance</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap rounded bg-bg p-3 text-xs text-ink-muted">{JSON.stringify(job.result, null, 2)}</pre></details>
    </div> : null}
  </article>;
}
