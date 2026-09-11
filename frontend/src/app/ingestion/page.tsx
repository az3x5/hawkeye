import { redirect } from "next/navigation";
import { BlackGlassIntake } from "@/app/ingestion/blackglass-intake";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function IngestionPage() {
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  const canWrite = identity.scopes.includes("media:write");
  const canRead = identity.scopes.includes("media:read");

  return (
    <>
      <PageHeader
        title="BlackGlass intake"
        description="Manually deliver exported BlackGlass evidence into EagleEye with idempotent source references and auditable provenance."
      />
      {canWrite ? (
        <BlackGlassIntake canRead={canRead} />
      ) : (
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Your account does not hold the <span className="identifier">media:write</span> scope.
          An administrator can grant it.
        </p>
      )}
    </>
  );
}
