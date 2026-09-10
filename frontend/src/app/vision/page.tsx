import { redirect } from "next/navigation";
import { MediaWorkspace } from "@/app/vision/media-workspace";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function VisionPage() {
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  return (
    <>
      <PageHeader
        title="Vision intelligence"
        description="Analyze bounded image, video and audio evidence with model provenance, visible limitations and operator review."
      />
      {identity.scopes.includes("language") ? (
        <MediaWorkspace />
      ) : (
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Your account does not hold the <span className="identifier">language</span> scope
          required by the current media service. An administrator can grant it.
        </p>
      )}
    </>
  );
}
