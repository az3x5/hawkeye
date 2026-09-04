import { StatusBadge } from "@/components/states/status-badge";
import type { DhivehiModelCapability } from "@/lib/types";

export function ModelStatus({ models }: { models: DhivehiModelCapability[] }) {
  return (
    <section className="panel mt-4" aria-labelledby="model-status-heading">
      <div className="border-b border-line px-4 py-3">
        <h2 id="model-status-heading" className="text-sm font-semibold text-ink">
          Dhivehi model status
        </h2>
        <p className="mt-0.5 text-xs text-ink-faint">
          Live inference-service state. Installed means the weights are on disk; loaded means
          they are currently in memory.
        </p>
      </div>
      <div className="divide-y divide-line">
        {models.map((model) => (
          <div key={model.task} className="grid gap-2 px-4 py-3 md:grid-cols-[12rem_1fr_auto]">
            <div>
              <p className="text-sm font-medium text-ink">{model.task.replaceAll("_", " ")}</p>
              <p className="text-xs text-ink-faint">{model.license}</p>
            </div>
            <div>
              <p className="identifier text-xs text-ink-muted">{model.model_id}</p>
              <p className="mt-1 text-xs text-ink-faint">{model.quality_summary}</p>
              <p className="mt-1 text-xs text-review">{model.limitation}</p>
            </div>
            <StatusBadge
              tone={model.loaded || model.status === "installed" ? "accept" : "reject"}
            >
              {model.loaded ? "loaded" : model.status.replaceAll("_", " ")}
            </StatusBadge>
          </div>
        ))}
      </div>
    </section>
  );
}
