import { Activity } from "lucide-react";
import { fetchReadiness } from "@/lib/api";
import { StatusBadge } from "@/components/states/status-badge";

/**
 * Whether the backend and its dependencies are usable.
 *
 * Reads the API's own readiness probe rather than inferring health from
 * whether the last request happened to succeed. Each dependency is named, so
 * an operator can tell "the vector store is down" from "everything is down".
 */
export async function HealthIndicator() {
  const readiness = await fetchReadiness();

  if (readiness === null) {
    return (
      <StatusBadge tone="reject">
        <Activity className="size-3" aria-hidden="true" />
        API unreachable
      </StatusBadge>
    );
  }

  const failing = readiness.checks.filter((check) => !check.healthy);
  const ready = readiness.status === "ready";

  return (
    <StatusBadge
      tone={ready ? "accept" : "reject"}
      className="max-w-[18rem]"
      title={
        failing.length > 0
          ? `Unhealthy: ${failing.map((check) => check.name).join(", ")}`
          : readiness.checks.map((check) => check.name).join(", ")
      }
    >
      <Activity className="size-3 shrink-0" aria-hidden="true" />
      <span className="truncate">
        {ready
          ? `API ready · ${readiness.checks.length} dependencies`
          : `Degraded · ${failing.map((check) => check.name).join(", ")}`}
      </span>
    </StatusBadge>
  );
}
