import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";

export const dynamic = "force-dynamic";

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Your account, credentials, and the decision policy in force." />
      <NotAvailable capability="settings" />
    </>
  );
}
