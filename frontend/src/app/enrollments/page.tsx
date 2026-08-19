import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";

export const dynamic = "force-dynamic";

export default function EnrollmentsPage() {
  return (
    <>
      <PageHeader title="Enrollments" description="Register faces against a person, and follow each sample through detection and embedding." />
      <NotAvailable capability="enrollments" />
    </>
  );
}
