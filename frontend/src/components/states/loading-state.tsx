import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Work in progress.
 *
 * Shaped like the content it replaces, so the layout does not jump when data
 * arrives, and announced to assistive technology rather than only drawn.
 */
export function LoadingState({
  label = "Loading",
  rows = 4,
  className,
}: {
  label?: string;
  rows?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("panel p-4", className)}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">{label}</span>
      <div className="space-y-2.5">
        {Array.from({ length: rows }, (_, index) => (
          <Skeleton
            key={index}
            className="h-9 w-full bg-surface-raised"
            style={{ opacity: 1 - index * 0.15 }}
          />
        ))}
      </div>
    </div>
  );
}
