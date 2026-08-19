import { cn } from "@/lib/utils";

/**
 * A short, colour-coded state.
 *
 * Colour is never the only signal: every variant carries its own words, so the
 * badge still reads correctly in monochrome or to someone who cannot
 * distinguish the hues.
 */
export type StatusTone = "accept" | "review" | "reject" | "neutral" | "info";

const TONES: Record<StatusTone, string> = {
  accept: "border-accept/35 bg-accept/12 text-accept",
  review: "border-review/35 bg-review/12 text-review",
  reject: "border-reject/35 bg-reject/12 text-reject",
  info: "border-accent/35 bg-accent/12 text-accent",
  neutral: "border-line-strong bg-surface-raised text-ink-muted",
};

export function StatusBadge({
  tone = "neutral",
  children,
  className,
  title,
}: {
  tone?: StatusTone;
  children: React.ReactNode;
  className?: string;
  /** Detail on hover, e.g. which dependency is failing. */
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
        "text-xs font-semibold whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Map a decision outcome onto a tone, so the mapping lives in one place. */
export function toneForOutcome(outcome: string): StatusTone {
  switch (outcome) {
    case "accept":
    case "confirmed":
    case "processed":
      return "accept";
    case "review":
    case "pending":
      return "review";
    case "reject":
    case "rejected":
    case "failed":
      return "reject";
    default:
      return "neutral";
  }
}
