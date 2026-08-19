import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Nothing to show, and nothing wrong.
 *
 * Distinct from an error and from an unavailable capability: an empty queue is
 * a normal operational state and should not read as a fault.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("panel px-6 py-12 text-center", className)}>
      {Icon ? (
        <Icon className="mx-auto mb-3 size-6 text-ink-faint" aria-hidden="true" />
      ) : null}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {description ? (
        <p className="mx-auto mt-1.5 max-w-prose text-sm text-ink-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
