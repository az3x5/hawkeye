"use client";

import { CheckCircle2, DatabaseZap, FileUp, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/states/status-badge";
import { Button } from "@/components/ui/button";

type Asset = {
  media_uuid: string;
  sha256: string;
  byte_size: number;
  media_type: string;
  mime_type: string;
  classification: string;
  status: string;
  created_at: string;
};
type IngestResult = {
  media: Asset;
  media_source: { source_uuid: string; source_system: string; external_source_id?: string | null };
  source_id: string;
  source_type: string;
  attributes: Record<string, unknown>;
  subject: { type: string; id: string };
  created: boolean;
  status: "accepted" | "already_exists";
  analysis_routes: Array<{ capability: string; state: string; endpoint?: string | null }>;
};
type MediaPage = { items: Asset[]; total: number; limit: number; offset: number };
type ApiFailure = { error?: { message?: string } };

const MAX_BYTES = 50 * 1024 * 1024;

function bytes(value: number) {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function BlackGlassIntake({ canRead }: { canRead: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [system, setSystem] = useState("blackglass-prod");
  const [externalId, setExternalId] = useState("");
  const [objectType, setObjectType] = useState("media");
  const [sourceUrl, setSourceUrl] = useState("");
  const [collectedAt, setCollectedAt] = useState("");
  const [classification, setClassification] = useState("internal");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<IngestResult | null>(null);
  const [recent, setRecent] = useState<Asset[]>([]);

  const refresh = useCallback(async () => {
    if (!canRead) return;
    try {
      const response = await fetch("/api/v1/media?limit=8", { cache: "no-store" });
      const body = (await response.json()) as MediaPage | ApiFailure;
      if (!response.ok) throw new Error("error" in body ? body.error?.message : "Unable to load media.");
      setRecent((body as MediaPage).items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load recent media.");
    }
  }, [canRead]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(initial);
  }, [refresh]);

  async function submit() {
    if (!file) return setError("Choose an exported BlackGlass file.");
    if (!system.trim()) return setError("Enter the BlackGlass environment name.");
    if (!externalId.trim()) return setError("Enter the BlackGlass record ID for idempotent redelivery.");
    if (file.size > MAX_BYTES) return setError("Choose a file below 50 MB for browser intake.");
    if (sourceUrl.trim()) {
      try {
        const url = new URL(sourceUrl.trim());
        if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error();
      } catch {
        return setError("Enter a valid HTTP or HTTPS BlackGlass record URL.");
      }
    }

    setBusy(true);
    setError("");
    setResult(null);
    const form = new FormData();
    form.set("file", file);
    form.set("source_system", system.trim());
    form.set("source_id", externalId.trim());
    form.set("source_type", objectType);
    form.set("classification", classification);
    form.set("collector_version", "hawkeye-web-manual/1");
    if (sourceUrl.trim()) form.set("source_url", sourceUrl.trim());
    if (collectedAt) form.set("collected_at", new Date(collectedAt).toISOString());

    try {
      const response = await fetch("/api/v1/integrations/blackglass/media", { method: "POST", body: form });
      const body = (await response.json()) as IngestResult | ApiFailure;
      if (!response.ok) {
        throw new Error("error" in body ? body.error?.message ?? "Ingestion failed." : "Ingestion failed.");
      }
      setResult(body as IngestResult);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Ingestion failed.");
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1.5 w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink outline-none focus:border-accent";

  return <div className="space-y-5">
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(19rem,0.8fr)]">
      <section className="panel overflow-hidden" aria-labelledby="delivery-title">
        <div className="flex items-center gap-3 border-b border-line px-5 py-4">
          <span className="rounded-md bg-accent/10 p-2 text-accent"><DatabaseZap className="size-5" /></span>
          <div><h2 id="delivery-title" className="font-semibold text-ink">Manual delivery</h2><p className="text-sm text-ink-muted">Upload bytes exported from one BlackGlass record.</p></div>
        </div>
        <div className="space-y-5 p-5">
          <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-surface-sunken px-5 py-6 text-center transition-colors hover:border-accent/70 hover:bg-accent/5">
            <FileUp className="mb-2 size-6 text-accent" />
            <span className="text-sm font-semibold text-ink">{file?.name ?? "Choose exported media or document"}</span>
            <span className="mt-1 text-xs text-ink-muted">Images, video, audio or PDF · maximum 50 MB through the browser</span>
            <input className="sr-only" type="file" accept="image/*,video/*,audio/*,application/pdf" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); }} />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm text-ink-muted">BlackGlass environment
              <input className={field} value={system} onChange={(event) => setSystem(event.target.value)} required maxLength={128} />
            </label>
            <label className="text-sm text-ink-muted">BlackGlass record ID
              <input className={field} value={externalId} onChange={(event) => setExternalId(event.target.value)} placeholder="bg-4471" required maxLength={256} />
            </label>
            <label className="text-sm text-ink-muted">Object type
              <select className={field} value={objectType} onChange={(event) => setObjectType(event.target.value)}><option value="media">Media</option><option value="post">Post attachment</option><option value="face_image">Face observation</option><option value="document">Document</option><option value="audio">Audio</option><option value="video">Video</option></select>
            </label>
            <label className="text-sm text-ink-muted sm:col-span-2">Original record URL <span className="text-ink-faint">(recorded only; never fetched)</span>
              <input className={field} type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://blackglass.example/records/bg-4471" maxLength={2048} />
            </label>
            <label className="text-sm text-ink-muted">Collected at
              <input className={field} type="datetime-local" value={collectedAt} onChange={(event) => setCollectedAt(event.target.value)} />
            </label>
            <label className="text-sm text-ink-muted">Classification
              <select className={field} value={classification} onChange={(event) => setClassification(event.target.value)}><option value="internal">Internal</option><option value="restricted">Restricted</option><option value="biometric">Biometric</option><option value="public">Public</option></select>
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-3"><Button type="button" disabled={busy || !file} onClick={() => void submit()}><DatabaseZap />{busy ? "Ingesting…" : "Ingest into EagleEye"}</Button>{file ? <span className="text-xs text-ink-muted">{bytes(file.size)}</span> : null}</div>
          {error ? <p role="alert" className="rounded-md border border-reject/30 bg-reject/10 px-3 py-2 text-sm text-reject">{error}</p> : null}
        </div>
      </section>

      <aside className="space-y-4">
        <div className="panel p-4"><div className="flex items-center gap-2"><ShieldCheck className="size-4 text-accept" /><h2 className="text-sm font-semibold text-ink">Ingestion guarantees</h2></div><ul className="mt-3 space-y-2 text-sm text-ink-muted"><li>File type is detected from its bytes.</li><li>Repeated content converges on one asset.</li><li>The BlackGlass record ID prevents duplicate provenance.</li><li>Every delivery is attributed to your account.</li><li>Source URLs are stored as evidence metadata, never downloaded.</li></ul></div>
        {result ? <div className="panel border-accept/25 p-4"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><CheckCircle2 className="size-4 text-accept" /><h2 className="text-sm font-semibold text-ink">Delivery accepted</h2></div><StatusBadge tone="accept">{result.status === "accepted" ? "accepted" : "already held"}</StatusBadge></div><dl className="mt-4 grid gap-3 text-xs"><div><dt className="text-ink-faint">EagleEye asset</dt><dd className="mt-1 break-all font-mono text-ink">{result.subject.id}</dd></div><div><dt className="text-ink-faint">Source record</dt><dd className="mt-1 break-all font-mono text-ink">{result.source_id}</dd></div><div><dt className="text-ink-faint">SHA-256</dt><dd className="mt-1 break-all font-mono text-ink-muted">{result.media.sha256}</dd></div></dl><div className="mt-4 flex flex-wrap gap-2">{result.analysis_routes.map((route) => <StatusBadge key={route.capability} tone={route.state === "queued" || route.state === "available_on_demand" ? "info" : "neutral"}>{route.capability}: {route.state.replaceAll("_", " ")}</StatusBadge>)}</div></div> : null}
      </aside>
    </div>

    {canRead ? <section aria-labelledby="recent-media-title"><div className="mb-3 flex items-center justify-between"><div><h2 id="recent-media-title" className="font-semibold text-ink">Recent stored assets</h2><p className="text-sm text-ink-muted">Newest media across permitted sources.</p></div><Button type="button" variant="ghost" size="sm" onClick={() => void refresh()}><RefreshCw />Refresh</Button></div><div className="panel overflow-x-auto"><table className="w-full min-w-[44rem] text-left text-sm"><thead className="border-b border-line text-xs text-ink-faint uppercase"><tr><th className="px-4 py-3">Type</th><th className="px-4 py-3">Asset ID</th><th className="px-4 py-3">Size</th><th className="px-4 py-3">Classification</th><th className="px-4 py-3">Created</th></tr></thead><tbody className="divide-y divide-line">{recent.map((asset) => <tr key={asset.media_uuid}><td className="px-4 py-3"><StatusBadge tone="info">{asset.media_type}</StatusBadge></td><td className="px-4 py-3 font-mono text-xs text-ink">{asset.media_uuid}</td><td className="px-4 py-3 text-ink-muted">{bytes(asset.byte_size)}</td><td className="px-4 py-3 text-ink-muted">{asset.classification}</td><td className="px-4 py-3 text-ink-muted">{new Date(asset.created_at).toLocaleString()}</td></tr>)}{recent.length === 0 ? <tr><td className="px-4 py-8 text-center text-ink-muted" colSpan={5}>No media assets are visible to this account yet.</td></tr> : null}</tbody></table></div></section> : null}
  </div>;
}
