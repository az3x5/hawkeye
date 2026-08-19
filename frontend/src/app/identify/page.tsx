import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";

export const dynamic = "force-dynamic";

export default function IdentifyPage() {
  return (
    <>
      <PageHeader title="Identify" description="Submit a face and see who the system proposes, with the policy that produced the decision." />
      <NotAvailable capability="identify" />
    </>
  );
}
