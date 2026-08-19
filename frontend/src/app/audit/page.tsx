import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";

export const dynamic = "force-dynamic";

export default function AuditPage() {
  return (
    <>
      <PageHeader title="Audit" description="Every administrative and review action, in the order it happened." />
      <NotAvailable capability="audit" />
    </>
  );
}
