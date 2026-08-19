import { bandFor, formatScore, scorePosition } from "@/lib/format";
import type { Thresholds } from "@/lib/types";

/**
 * Where a score sits against the policy in force.
 *
 * A reviewer otherwise has to hold three numbers in their head and compare them
 * mentally. The bar is a coordinate, not a confidence: the raw similarity is
 * always printed beside it, and the axis is labelled with the thresholds rather
 * than with percentages.
 */
export function ScoreScale({
  score,
  thresholds,
}: {
  score: number;
  thresholds: Thresholds;
}) {
  const review = scorePosition(thresholds.review_at);
  const accept = scorePosition(thresholds.accept_at);
  const position = scorePosition(score);
  const band = bandFor(score, thresholds);

  return (
    <div className="scale">
      <div
        className="scale-track"
        style={
          {
            "--review-stop": `${review * 100}%`,
            "--accept-stop": `${accept * 100}%`,
          } as React.CSSProperties
        }
        role="img"
        aria-label={
          `Similarity ${formatScore(score)} against policy ` +
          `${thresholds.policy_version}: review at ${formatScore(thresholds.review_at)}, ` +
          `accept at ${formatScore(thresholds.accept_at)}.`
        }
      >
        <span className="scale-tick" style={{ left: `${review * 100}%` }} />
        <span className="scale-tick" style={{ left: `${accept * 100}%` }} />
        <span className={`scale-marker ${band}`} style={{ left: `${position * 100}%` }} />
      </div>
      <div className="scale-legend">
        <span>0.0000</span>
        <span>review {formatScore(thresholds.review_at)}</span>
        <span>accept {formatScore(thresholds.accept_at)}</span>
        <span>1.0000</span>
      </div>
    </div>
  );
}
