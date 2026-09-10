"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

/** Keep public authentication routes visually separate from the operator workstation. */
export function ApplicationShell({
  children,
  header,
  sidebar,
}: {
  children: ReactNode;
  header: ReactNode;
  sidebar: ReactNode;
}) {
  const pathname = usePathname();

  if (pathname === "/sign-in") {
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-12">
        <div
          className="pointer-events-none absolute inset-0 opacity-50"
          aria-hidden="true"
          style={{
            background:
              "radial-gradient(circle at 20% 15%, rgba(24, 182, 213, 0.14), transparent 32%), radial-gradient(circle at 80% 85%, rgba(24, 182, 213, 0.08), transparent 30%)",
          }}
        />
        <div className="relative w-full max-w-md">
          <div className="mb-6 flex items-center justify-center gap-2.5 text-sm font-semibold tracking-wide text-ink">
            <span className="size-2 rounded-full bg-accent shadow-[0_0_18px_rgba(24,182,213,0.9)]" />
            HAWKEYE
            <span className="font-normal text-ink-faint">INTELLIGENCE</span>
          </div>
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col">
        {header}
        <main className="mx-auto w-full max-w-[100rem] flex-1 p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
