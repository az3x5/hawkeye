import { redirect } from "next/navigation";
import { EnrolConsole } from "@/app/enrollments/enrol-console";
import { PageHeader } from "@/components/shell/page-header";
import { NotAvailable } from "@/components/states/not-available";
import { fetchIdentity } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function EnrollmentsPage() {
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  const canEnrol = identity.scopes.includes("enrol");

  return (
    <>
      <PageHeader
        title="Enrollments"
        description="Register a face against a person, and follow the sample through detection and embedding."
      />

      {canEnrol ? (
        <div className="space-y-4">
          {/* Images come through the proxy, which requires the review scope. */}
          <EnrolConsole canSeeImages={identity.scopes.includes("review")} />
          <NotAvailable capability="enrollments" />
        </div>
      ) : (
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Your account does not hold the <span className="identifier">enrol</span> scope. An
          administrator can grant it.
        </p>
      )}
    </>
  );
}
