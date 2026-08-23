import Link from "next/link";
import { Nav } from "@/components/shell/nav";

/** The desktop sidebar. Collapses to the drawer below the `lg` breakpoint. */
export function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface lg:flex">
      <div className="flex h-14 items-center border-b border-line px-4">
        <Link href="/dashboard" className="flex items-center gap-2 text-sm font-semibold">
          <span className="size-2 rounded-full bg-accent" aria-hidden="true" />
          Hawkeye
          <span className="font-normal text-ink-faint">face&nbsp;id</span>
        </Link>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        <Nav />
      </div>
      <div className="border-t border-line p-3">
        <p className="text-[0.6875rem] leading-relaxed text-ink-faint">
          Hawkeye
          <br />
          Face ID module · Person Intelligence
        </p>
      </div>
    </aside>
  );
}
