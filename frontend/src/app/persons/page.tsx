import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";

export const dynamic = "force-dynamic";

export default function PersonsPage() {
  return (
    <>
      <PageHeader title="Persons" description="The people known to the system, their samples and their external identifiers." />
      <NotAvailable capability="persons" />
    </>
  );
}
