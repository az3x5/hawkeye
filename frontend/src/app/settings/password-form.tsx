"use client";

import { KeyRound } from "lucide-react";
import { useActionState } from "react";
import { changePasswordAction, type ActionResult } from "@/app/settings/actions";

const FIELD = "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink";

/**
 * Change your own password.
 *
 * The current password is required by the API, so a borrowed session cannot be
 * turned into permanent ownership of the account. Changing it revokes every
 * session, including this one — said plainly, because being signed out
 * immediately afterwards would otherwise look like a fault.
 */
export function PasswordForm() {
  const [result, action, pending] = useActionState<ActionResult | null, FormData>(
    changePasswordAction,
    null,
  );

  return (
    <form action={action} className="max-w-sm space-y-3">
      <label className="block space-y-1.5 text-sm text-ink-muted">
        Current password
        <input type="password" name="current_password" autoComplete="current-password" className={FIELD} required />
      </label>
      <label className="block space-y-1.5 text-sm text-ink-muted">
        New password
        <input type="password" name="new_password" autoComplete="new-password" className={FIELD} required />
      </label>
      <label className="block space-y-1.5 text-sm text-ink-muted">
        Repeat new password
        <input type="password" name="confirm_password" autoComplete="new-password" className={FIELD} required />
      </label>

      {result === null ? null : (
        <p
          className={`rounded-md border-l-2 px-3 py-2 text-sm ${
            result.ok
              ? "border-accept bg-accept/10 text-accept"
              : "border-reject bg-reject/10 text-reject"
          }`}
        >
          {result.message}
        </p>
      )}

      <button
        type="submit"
        disabled={pending}
        className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
      >
        <KeyRound className="size-4" aria-hidden="true" />
        {pending ? "Changing…" : "Change password"}
      </button>
      <p className="text-xs text-ink-faint">
        At least 12 characters. Changing it signs out every session, including this one.
      </p>
    </form>
  );
}
