import { bandFor, formatScore, scorePosition } from "@/lib/format";
import type { Thresholds } from "@/lib/types";

const MARKER: Record<string, string> = {
  accept: "bg-accept",
  review: "bg-review",
  reject: "bg-reject",
};

/**
 * Where a score sits against the policy in force.
 *
 * A reviewer otherwise holds three numbers in their head and compares them
 * mentally. The bar is a coordinate for drawing, not a confidence: the axis is
 * labelled with the thresholds themselves and the raw similarity is printed
 * beside it.
 */
export function ScoreScale({
  score,
  thresholds,
}: {
  score: number;
  thresholds: Thresholds;
}) {
  const review = scorePosition(thresholds.review_at) * 100;
  const accept = scorePosition(thresholds.accept_at) * 100;
  const position = scorePosition(score) * 100;

  return (
    <div className="space-y-1.5">
      <div
        className="relative h-2.5 rounded-full border border-line"
        role="img"
        aria-label={
          `Similarity ${formatScore(score)} against policy ${thresholds.policy_version}: ` +
          `review at ${formatScore(thresholds.review_at)}, accept at ${formatScore(thresholds.accept_at)}.`
        }
        style={{
          background:
            `linear-gradient(to right, rgb(224 112 92 / 18%) 0%, rgb(224 112 92 / 18%) ${review}%, ` +
            `rgb(217 164 65 / 20%) ${review}%, rgb(217 164 65 / 20%) ${accept}%, ` +
            `rgb(63 178 127 / 20%) ${accept}%, rgb(63 178 127 / 20%) 100%)`,
        }}
      >
        <span
          className="absolute -top-1 -bottom-1 w-px bg-line-strong"
          style={{ left: `${review}%` }}
        />
        <span
          className="absolute -top-1 -bottom-1 w-px bg-line-strong"
          style={{ left: `${accept}%` }}
        />
        <span
          className={`absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface ${MARKER[bandFor(score, thresholds)]}`}
          style={{ left: `${position}%` }}
        />
      </div>
      <div className="flex justify-between text-[0.6875rem] text-ink-faint tabular-nums">
        <span>0.0000</span>
        <span>review {formatScore(thresholds.review_at)}</span>
        <span>accept {formatScore(thresholds.accept_at)}</span>
        <span>1.0000</span>
      </div>
    </div>
  );
}
