/**
 * The navigation model.
 *
 * Grouped by what an operator is doing rather than by the system's internals:
 * work they perform, intelligence they consult, and the system itself.
 */

import {
  ClipboardCheck,
  FileClock,
  Fingerprint,
  LayoutDashboard,
  ScanFace,
  Settings,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { CapabilityKey } from "@/lib/capabilities";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  capability?: CapabilityKey;
}

export interface NavGroup {
  label: string | null;
  items: NavItem[];
}

export const NAVIGATION: NavGroup[] = [
  {
    label: null,
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: LayoutDashboard,
        capability: "dashboard",
      },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/identify", label: "Identify", icon: ScanFace, capability: "identify" },
      {
        href: "/enrollments",
        label: "Enrollments",
        icon: Fingerprint,
        capability: "enrollments",
      },
      { href: "/review", label: "Review", icon: ClipboardCheck, capability: "review" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/persons", label: "Persons", icon: Users, capability: "persons" },
      { href: "/matches", label: "Matches", icon: ShieldCheck, capability: "matches" },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/audit", label: "Audit", icon: FileClock, capability: "audit" },
      { href: "/settings", label: "Settings", icon: Settings, capability: "settings" },
    ],
  },
];

/** Human-readable name for a path, for breadcrumbs and titles. */
export function labelForPath(pathname: string): string {
  const match = NAVIGATION.flatMap((group) => group.items).find((item) =>
    pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  return match?.label ?? "Face ID";
}
