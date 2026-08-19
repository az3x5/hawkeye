"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAVIGATION } from "@/lib/navigation";

/** Where the current page sits, derived from the path and the nav model. */
export function Breadcrumbs() {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return null;

  const root = `/${segments[0]}`;
  const group = NAVIGATION.find((candidate) =>
    candidate.items.some((item) => item.href === root),
  );
  const item = group?.items.find((candidate) => candidate.href === root);

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm">
      {group?.label ? (
        <>
          <span className="text-ink-faint">{group.label}</span>
          <ChevronRight className="size-3.5 text-ink-faint" aria-hidden="true" />
        </>
      ) : null}

      {segments.length > 1 && item ? (
        <>
          <Link href={item.href} className="text-ink-muted hover:text-ink">
            {item.label}
          </Link>
          <ChevronRight className="size-3.5 text-ink-faint" aria-hidden="true" />
          <span className="identifier text-ink">{segments.slice(1).join("/")}</span>
        </>
      ) : (
        <span className="font-semibold text-ink">{item?.label ?? "Face ID"}</span>
      )}
    </nav>
  );
}
