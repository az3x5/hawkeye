import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";

export const dynamic = "force-dynamic";

export default function MatchesPage() {
  return (
    <>
      <PageHeader title="Matches" description="Identification history: what was proposed, what a reviewer concluded, and under which policy." />
      <NotAvailable capability="matches" />
    </>
  );
}
