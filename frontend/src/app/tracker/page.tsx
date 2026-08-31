import { redirect } from "next/navigation";
import { CameraTracker } from "@/app/tracker/camera-tracker";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TrackerPage() {
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  return (
    <>
      <PageHeader
        title="Live sources"
        description="Analyze a device camera or shared CCTV screen for motion and known people."
      />
      {identity.scopes.includes("identify") ? (
        <CameraTracker />
      ) : (
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Your account does not hold the <span className="identifier">identify</span> scope.
          An administrator can grant it.
        </p>
      )}
    </>
  );
}
