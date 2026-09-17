import { redirect } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";
import { EvidenceWorkspace } from "../workspace";

export const dynamic = "force-dynamic";

export default async function EvidenceReportPage({ params }: { params: Promise<{ analysisId: string }> }) {
  const identity = await fetchIdentity().catch(() => null);
  if (!identity) redirect("/sign-in");
  const { analysisId } = await params;
  return <>
    <PageHeader title="Evidence report" description="Persistent BlackGlass evidence analysis with source-linked findings." />
    {identity.scopes.includes("media:read") ? <EvidenceWorkspace
      canText={identity.scopes.includes("language")}
      canMedia={identity.scopes.includes("media:write")}
      initialAnalysisId={analysisId}
      reportOnly
    /> : <p className="panel p-6">Your account needs permission to read evidence.</p>}
  </>;
}
