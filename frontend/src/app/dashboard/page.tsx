import { NotAvailable } from "@/components/states/not-available";
import { PageHeader } from "@/components/shell/page-header";
import { SystemStatus } from "@/components/system-status";

export const dynamic = "force-dynamic";

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="System state, and operational figures once the API can report them."
      />
      {/* Real, from the API's own probes — the only thing here that is not
          speculation about a capability the backend does not have. */}
      <SystemStatus />
      <div className="mt-4">
        <NotAvailable capability="dashboard" />
      </div>
    </>
  );
}
