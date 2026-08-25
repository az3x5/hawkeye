import { redirect } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { SystemStatus } from "@/components/system-status";
import { HardwareUsage } from "@/components/hardware-usage";
import { LiveStatistics } from "@/app/dashboard/live-statistics";
import { ApiError, NotAuthenticatedError, fetchStatistics, fetchSystemMetrics } from "@/lib/api";
import type { Statistics, SystemMetrics } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let stats: Statistics | null = null;
  let systemMetrics: SystemMetrics | null = null;
  let problem: string | null = null;

  try {
    stats = await fetchStatistics();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    problem =
      error instanceof ApiError ? error.message : "Counts could not be read from the API.";
  }

  try {
    systemMetrics = await fetchSystemMetrics();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="What the system currently holds. Counts only — the system keeps no time series, so there are no rates or trends to show."
      />

      <div className="space-y-4">
        <LiveStatistics initial={stats} initialProblem={problem} />

        <HardwareUsage initial={systemMetrics} />
        <SystemStatus />
      </div>
    </>
  );
}
