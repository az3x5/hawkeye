import { LogOut } from "lucide-react";
import { fetchIdentity } from "@/lib/api";

/**
 * Who the operator is signed in as.
 *
 * Always visible, because decisions are attributed to this identity and nobody
 * should have to guess which one they are acting under.
 */
export async function WhoAmI() {
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) return null;

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-xs text-ink-muted sm:inline">{identity.subject}</span>
      <form action="/sign-out" method="post">
        <button
          type="submit"
          aria-label="Sign out"
          title="Sign out"
          className="rounded-md p-1.5 text-ink-muted hover:bg-surface-raised hover:text-ink"
        >
          <LogOut className="size-4" />
        </button>
      </form>
    </div>
  );
}
