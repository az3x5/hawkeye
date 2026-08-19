import { Suspense } from "react";
import { Breadcrumbs } from "@/components/shell/breadcrumbs";
import { WhoAmI } from "@/components/shell/whoami";
import { HealthIndicator } from "@/components/shell/health-indicator";
import { MobileNav } from "@/components/shell/mobile-nav";
import { Skeleton } from "@/components/ui/skeleton";

/** The top bar: navigation trigger, location, and system status. */
export function Header() {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur">
      <MobileNav />
      <div className="min-w-0 flex-1">
        <Breadcrumbs />
      </div>
      {/* Streamed: a slow or unreachable API must not hold up the page. */}
      <Suspense fallback={<Skeleton className="h-5 w-32 bg-surface-raised" />}>
        <HealthIndicator />
      </Suspense>
      <Suspense fallback={null}>
        <WhoAmI />
      </Suspense>
    </header>
  );
}
