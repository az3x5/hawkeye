import { PlugZap } from "lucide-react";
import { CAPABILITIES, type CapabilityKey } from "@/lib/capabilities";
import { StatusBadge } from "@/components/states/status-badge";

/**
 * A screen the backend cannot support yet.
 *
 * States the gap in terms of endpoints rather than apologising, because the
 * question an operator or an engineer actually has is *what is missing*. This
 * is deliberately not a placeholder dashboard: showing invented figures would
 * make an incomplete system indistinguishable from a working one.
 */
export function NotAvailable({ capability }: { capability: CapabilityKey }) {
  const { state, supported, missing, note } = CAPABILITIES[capability];

  return (
    <div className="panel p-6">
      <div className="flex items-start gap-3">
        <PlugZap className="mt-0.5 size-5 shrink-0 text-ink-faint" aria-hidden="true" />
        <div className="min-w-0 space-y-4">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-ink">
                {state === "partial"
                  ? "Partly supported by the API"
                  : "Not available yet"}
              </h2>
              <StatusBadge tone={state === "partial" ? "review" : "neutral"}>
                {state === "partial" ? "partial backend" : "no backend"}
              </StatusBadge>
            </div>
            <p className="max-w-prose text-sm text-ink-muted">{note}</p>
          </div>

          {supported.length > 0 ? (
            <div>
              <p className="text-xs font-semibold tracking-wide text-ink-faint uppercase">
                Supported today
              </p>
              <ul className="mt-1.5 space-y-1">
                {supported.map((endpoint) => (
                  <li key={endpoint} className="identifier">
                    {endpoint}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {missing.length > 0 ? (
            <div>
              <p className="text-xs font-semibold tracking-wide text-ink-faint uppercase">
                Needed before this screen can be built
              </p>
              <ul className="mt-1.5 space-y-1">
                {missing.map((gap) => (
                  <li key={gap} className="text-sm text-ink-muted">
                    {gap}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
