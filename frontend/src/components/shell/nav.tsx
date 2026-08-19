"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CAPABILITIES } from "@/lib/capabilities";
import { NAVIGATION } from "@/lib/navigation";
import { cn } from "@/lib/utils";

/**
 * The navigation list, shared by the desktop sidebar and the mobile drawer.
 *
 * Screens the backend cannot support yet are still reachable — hiding them
 * would misrepresent the shape of the product — but they are marked, so nobody
 * walks into one expecting it to work.
 */
export function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav className="space-y-6" aria-label="Primary">
      {NAVIGATION.map((group, index) => (
        <div key={group.label ?? `group-${index}`}>
          {group.label ? (
            <p className="mb-1.5 px-3 text-[0.6875rem] font-semibold tracking-[0.08em] text-ink-faint uppercase">
              {group.label}
            </p>
          ) : null}
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(`${item.href}/`);
              const capability = item.capability
                ? CAPABILITIES[item.capability].state
                : "available";

              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                      active
                        ? "bg-surface-raised font-semibold text-ink"
                        : "text-ink-muted hover:bg-surface-raised/60 hover:text-ink",
                    )}
                  >
                    <item.icon className="size-4 shrink-0" aria-hidden="true" />
                    <span className="flex-1 truncate">{item.label}</span>
                    {capability === "unavailable" ? (
                      <span
                        className="size-1.5 shrink-0 rounded-full bg-ink-faint"
                        title="No backend support yet"
                        aria-label="No backend support yet"
                      />
                    ) : capability === "partial" ? (
                      <span
                        className="size-1.5 shrink-0 rounded-full bg-review"
                        title="Partial backend support"
                        aria-label="Partial backend support"
                      />
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
