"use client";

import { useEffect, useState } from "react";
import { Cpu, HardDrive, MemoryStick, Microchip } from "lucide-react";
import type { CapacityUsage, ProcessingMetrics, SystemMetrics } from "@/lib/types";

const REFRESH_MS = 5_000;

export function HardwareUsage({ initial }: { initial: SystemMetrics | null }) {
  const [metrics, setMetrics] = useState(initial);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const response = await fetch("/api/v1/system/metrics", {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = (await response.json()) as SystemMetrics;
        if (active) {
          setMetrics(next);
          setStale(false);
        }
      } catch {
        if (active) setStale(true);
      }
    }

    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <section className="panel" aria-labelledby="hardware-usage">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 id="hardware-usage" className="text-sm font-semibold text-ink">
            Hardware usage
          </h2>
          <p className="mt-0.5 text-xs text-ink-faint">Live server load, refreshed every 5 seconds</p>
        </div>
        <span className={`text-xs ${stale ? "text-review" : "text-ink-faint"}`}>
          {stale ? "Update delayed" : metrics ? "Live" : "Waiting for data"}
        </span>
      </div>

      {metrics === null ? (
        <p className="px-4 py-6 text-sm text-ink-muted">Hardware metrics are unavailable.</p>
      ) : (
        <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-4">
          <UsageCard
            label="CPU"
            icon={Cpu}
            percent={metrics.cpu_percent}
            detail={`${metrics.cpu_count} logical cores`}
          />
          <UsageCard
            label="Memory"
            icon={MemoryStick}
            percent={metrics.memory.percent}
            detail={capacityDetail(metrics.memory)}
          />
          <UsageCard
            label="Disk"
            icon={HardDrive}
            percent={metrics.disk.percent}
            detail={capacityDetail(metrics.disk)}
          />
          {metrics.gpus.length > 0 ? (
            <UsageCard
              label="GPU"
              icon={Microchip}
              percent={Math.max(...metrics.gpus.map((gpu) => gpu.utilization_percent))}
              detail={
                metrics.gpus.length === 1
                  ? `${metrics.gpus[0]!.name} · ${capacityDetail(metrics.gpus[0]!.memory)} VRAM`
                  : `${metrics.gpus.length} GPUs · peak utilization`
              }
            />
          ) : (
            <UnavailableGpu reason={metrics.gpu_error} />
          )}
        </div>
      )}
      {metrics?.processing ? <ProcessingUsage value={metrics.processing} /> : null}
    </section>
  );
}


function ProcessingUsage({ value }: { value: ProcessingMetrics }) {
  const failed = (value.counts.failed ?? 0) + (value.counts.dead_letter ?? 0);
  const age =
    value.oldest_queued_age_seconds === null
      ? "No queued work"
      : `Oldest waiting ${formatDuration(value.oldest_queued_age_seconds)}`;

  return (
    <div className="border-t border-line" aria-labelledby="processing-usage">
      <div className="px-4 py-3">
        <h3 id="processing-usage" className="text-sm font-semibold text-ink">
          Durable processing
        </h3>
        <p className="mt-0.5 text-xs text-ink-faint">PostgreSQL queue state from the same live refresh</p>
      </div>
      <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          label="Queue depth"
          value={value.queue_depth}
          detail={`${value.counts.queued ?? 0} queued · ${value.counts.retry ?? 0} retry`}
        />
        <MetricCard
          label="Active leases"
          value={value.active_leases}
          detail={`${value.expired_leases} expired`}
          warning={value.expired_leases > 0}
        />
        <MetricCard
          label="Failed"
          value={failed}
          detail={`${value.counts.dead_letter ?? 0} dead letter`}
          warning={failed > 0}
        />
        <MetricCard
          label="Completed / min"
          value={value.completed_last_minute}
          detail={`${value.attempts_last_minute} attempts`}
        />
        <MetricCard label="Live workers" value={value.live_workers} detail={age} />
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  detail,
  warning = false,
}: {
  label: string;
  value: number;
  detail: string;
  warning?: boolean;
}) {
  return (
    <div className="bg-surface p-4">
      <p className="text-xs font-medium text-ink-muted">{label}</p>
      <p className={`mt-2 text-2xl font-semibold tabular-nums ${warning ? "text-review" : "text-ink"}`}>
        {value}
      </p>
      <p className="mt-2 truncate text-xs text-ink-faint" title={detail}>
        {detail}
      </p>
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}
type Icon = typeof Cpu;

function UsageCard({
  label,
  icon: Icon,
  percent,
  detail,
}: {
  label: string;
  icon: Icon;
  percent: number;
  detail: string;
}) {
  const bounded = Math.min(Math.max(percent, 0), 100);
  const bar = bounded >= 95 ? "bg-reject" : bounded >= 80 ? "bg-review" : "bg-accent";

  return (
    <div className="bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-ink-muted">
          <Icon className="size-4" aria-hidden="true" />
          <span className="text-xs font-medium">{label}</span>
        </div>
        <span className="text-lg font-semibold tabular-nums text-ink">{bounded.toFixed(1)}%</span>
      </div>
      <div
        className="mt-3 h-2 overflow-hidden rounded-full bg-surface-sunken"
        role="progressbar"
        aria-label={`${label} utilization`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(bounded)}
      >
        <div className={`h-full rounded-full transition-[width] duration-500 ${bar}`} style={{ width: `${bounded}%` }} />
      </div>
      <p className="mt-2 truncate text-xs text-ink-faint" title={detail}>
        {detail}
      </p>
    </div>
  );
}

function UnavailableGpu({ reason }: { reason: string | null }) {
  return (
    <div className="bg-surface p-4">
      <div className="flex items-center gap-2 text-ink-muted">
        <Microchip className="size-4" aria-hidden="true" />
        <span className="text-xs font-medium">GPU</span>
      </div>
      <p className="mt-2 text-lg font-semibold text-ink">Unavailable</p>
      <p className="mt-2 line-clamp-2 text-xs text-ink-faint">
        {reason ?? "No supported GPU was detected"}
      </p>
    </div>
  );
}

function capacityDetail(value: CapacityUsage): string {
  return `${formatBytes(value.used_bytes)} of ${formatBytes(value.total_bytes)}`;
}

function formatBytes(value: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit < 3 ? 0 : 1)} ${units[unit]}`;
}
