import { redirect } from "next/navigation";

/**
 * One identification.
 *
 * The review screen already renders an identification in full — query beside
 * candidates, scores against the policy, and the recorded decision — so this
 * sends the reader there rather than maintaining a second view of the same
 * record.
 */
export default async function MatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/review/${id}`);
}
