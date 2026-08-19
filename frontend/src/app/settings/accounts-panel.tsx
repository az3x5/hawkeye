"use client";

import { UserPlus } from "lucide-react";
import { useActionState, useState, useTransition } from "react";
import {
  createAccountAction,
  setAccountDisabledAction,
  type ActionResult,
} from "@/app/settings/actions";
import { ScopePicker } from "@/app/settings/scope-picker";
import { StatusBadge } from "@/components/states/status-badge";
import { formatTime } from "@/lib/format";
import type { Account } from "@/lib/types";

const FIELD = "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink";

/** Password accounts: who exists, what they may do, and creating more. */
export function AccountsPanel({
  accounts,
  currentSubject,
}: {
  accounts: Account[];
  currentSubject: string;
}) {
  const [result, action, pending] = useActionState<ActionResult | null, FormData>(
    createAccountAction,
    null,
  );
  const [notice, setNotice] = useState<ActionResult | null>(null);
  const [busy, startTransition] = useTransition();
  const [creating, setCreating] = useState(false);

  function toggle(account: Account) {
    startTransition(async () => {
      setNotice(await setAccountDisabledAction(account.user_uuid, account.active));
    });
  }

  return (
    <section className="panel" aria-labelledby="accounts-heading">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 id="accounts-heading" className="text-sm font-semibold text-ink">
          Accounts
        </h2>
        <button
          type="button"
          onClick={() => setCreating((open) => !open)}
          className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-sm text-ink-muted hover:text-ink"
        >
          <UserPlus className="size-4" aria-hidden="true" />
          {creating ? "Cancel" : "New account"}
        </button>
      </div>

      {creating ? (
        <form action={action} className="max-w-sm space-y-3 border-b border-line p-4">
          <label className="block space-y-1.5 text-sm text-ink-muted">
            Email
            <input type="email" name="email" autoComplete="off" className={FIELD} required />
          </label>
          <label className="block space-y-1.5 text-sm text-ink-muted">
            Password
            <input type="password" name="password" autoComplete="new-password" className={FIELD} required />
          </label>
          <ScopePicker />
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
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {pending ? "Creating…" : "Create account"}
          </button>
        </form>
      ) : null}

      {notice === null ? null : (
        <p
          className={`border-b border-line px-4 py-2 text-sm ${
            notice.ok ? "text-accept" : "text-reject"
          }`}
        >
          {notice.message}
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Scopes</th>
              <th scope="col">Last sign-in</th>
              <th scope="col">State</th>
              <th scope="col" className="text-right">
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((account) => {
              const self = account.email === currentSubject;
              return (
                <tr key={account.user_uuid}>
                  <td className="text-ink">
                    {account.email}
                    {self ? <span className="ml-2 text-xs text-ink-faint">you</span> : null}
                  </td>
                  <td className="identifier">{account.scopes.join(", ")}</td>
                  <td className="text-ink-muted">
                    {account.last_login_at === null ? "never" : formatTime(account.last_login_at)}
                  </td>
                  <td>
                    <StatusBadge tone={account.active ? "accept" : "neutral"}>
                      {account.active ? "active" : "disabled"}
                    </StatusBadge>
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      onClick={() => toggle(account)}
                      /* Disabling your own account is refused by the API; not
                         offering it here avoids inviting the mistake. */
                      disabled={busy || (self && account.active)}
                      title={
                        self && account.active
                          ? "You cannot disable the account you are signed in as"
                          : undefined
                      }
                      className="rounded-md border border-line px-2.5 py-1 text-xs text-ink-muted hover:text-ink disabled:opacity-40"
                    >
                      {account.active ? "Disable" : "Enable"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
