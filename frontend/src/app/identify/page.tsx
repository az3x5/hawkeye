import { redirect } from "next/navigation";
import { IdentifyConsole } from "@/app/identify/identify-console";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function IdentifyPage() {
  // Identification needs the `identify` scope; sending someone to a console
  // that will refuse them is worse than saying so.
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  const permitted = identity.scopes.includes("identify");

  return (
    <>
      <PageHeader
        title="Identify"
        description="Submit a face and see who the system proposes. Scores are raw cosine similarities in [-1, 1] — not probabilities."
      />
      {permitted ? (
        <IdentifyConsole />
      ) : (
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Your account does not hold the <span className="identifier">identify</span> scope.
          An administrator can grant it.
        </p>
      )}
    </>
  );
}
