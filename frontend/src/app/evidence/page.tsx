import { redirect } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";
import { EvidenceWorkspace } from "./workspace";

export const dynamic = "force-dynamic";

export default async function EvidencePage() {
  const identity = await fetchIdentity().catch(() => null);
  if (!identity) redirect("/sign-in");
  return <>
    <PageHeader title="Evidence analysis" description="Submit source material, follow processing and inspect cited findings." />
    {identity.scopes.includes("media:read") ? <EvidenceWorkspace
      canText={identity.scopes.includes("language")}
      canMedia={identity.scopes.includes("media:write")}
    /> : <p className="panel p-6">Your account needs permission to read evidence.</p>}
  </>;
}
