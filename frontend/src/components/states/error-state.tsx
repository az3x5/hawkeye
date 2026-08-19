import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Something failed.
 *
 * Shows the API's own message where there is one: it explains the cause more
 * precisely than a generic apology, and an operator needs the detail to know
 * whether to retry, escalate or check a dependency.
 */
export function ErrorState({
  title = "Something went wrong",
  message,
  code,
  action,
  className,
}: {
  title?: string;
  message: string;
  code?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("panel border-reject/40 p-4", className)} role="alert">
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-reject" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">{title}</p>
          <p className="mt-1 text-sm text-ink-muted">{message}</p>
          {code ? <p className="identifier mt-2">{code}</p> : null}
          {action ? <div className="mt-3">{action}</div> : null}
        </div>
      </div>
    </div>
  );
}
