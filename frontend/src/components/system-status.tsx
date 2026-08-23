import { CheckCircle2, Server, XCircle } from "lucide-react";
import { fetchHealth, fetchReadiness } from "@/lib/api";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge } from "@/components/states/status-badge";

/**
 * The backend's own view of itself.
 *
 * Every value here comes from `/health` and `/readyz`; nothing is inferred or
 * filled in. When the API cannot be reached, that is what it says — an
 * unreachable backend is a fact worth showing, not a blank panel.
 */
export async function SystemStatus() {
  const [health, readiness] = await Promise.all([fetchHealth(), fetchReadiness()]);

  if (readiness === null) {
    return (
      <ErrorState
        title="API unreachable"
        message={
          "The Hawkeye API did not respond. Check that the backend is running and " +
          "that FACEID_API_URL points at it."
        }
      />
    );
  }

  return (
    <section className="panel" aria-labelledby="system-status">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <Server className="size-4 text-ink-faint" aria-hidden="true" />
          <h2 id="system-status" className="text-sm font-semibold text-ink">
            System status
          </h2>
        </div>
        <StatusBadge tone={readiness.status === "ready" ? "accept" : "reject"}>
          {readiness.status === "ready" ? "ready" : "not ready"}
        </StatusBadge>
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-b border-line px-4 py-3 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-ink-faint">Service</dt>
          <dd className="identifier text-ink">{health?.service ?? "unknown"}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-faint">Environment</dt>
          <dd className="identifier text-ink">{health?.environment ?? "unknown"}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-faint">Dependencies</dt>
          <dd className="text-sm text-ink">{readiness.checks.length}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-faint">Liveness</dt>
          <dd className="text-sm text-ink">{health === null ? "no answer" : "ok"}</dd>
        </div>
      </dl>

      {readiness.checks.length === 0 ? (
        <p className="px-4 py-3 text-sm text-ink-muted">
          The API registered no dependency probes.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {readiness.checks.map((check) => (
            <li key={check.name} className="flex items-start gap-3 px-4 py-2.5">
              {check.healthy ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-accept" aria-hidden="true" />
              ) : (
                <XCircle className="mt-0.5 size-4 shrink-0 text-reject" aria-hidden="true" />
              )}
              <div className="min-w-0">
                <p className="text-sm text-ink">{check.name}</p>
                {check.error ? (
                  <p className="identifier mt-0.5 break-words text-reject">{check.error}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
