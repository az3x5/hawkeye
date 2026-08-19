import { redirect } from "next/navigation";
import { AccountsPanel } from "@/app/settings/accounts-panel";
import { PasswordForm } from "@/app/settings/password-form";
import { TokensPanel } from "@/app/settings/tokens-panel";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState } from "@/components/states/error-state";
import { StatusBadge } from "@/components/states/status-badge";
import { ApiError, NotAuthenticatedError, fetchAccounts, fetchIdentity, fetchTokens } from "@/lib/api";
import { formatTime, shortId } from "@/lib/format";
import type { Account, TokenRecord } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let identity;
  try {
    identity = await fetchIdentity();
  } catch (error) {
    if (error instanceof NotAuthenticatedError) redirect("/sign-in");
    if (error instanceof ApiError && error.status === 401) redirect("/sign-in");
    return (
      <>
        <PageHeader title="Settings" />
        <ErrorState message="Could not read your account from the API." />
      </>
    );
  }

  const isAdmin = identity.scopes.includes("admin");

  // Administration is fetched only when the caller can actually use it, so a
  // reviewer's page does not turn into a wall of 403s.
  let accounts: Account[] = [];
  let tokens: TokenRecord[] = [];
  let adminError: string | null = null;
  if (isAdmin) {
    try {
      [accounts, tokens] = await Promise.all([fetchAccounts(), fetchTokens()]);
    } catch (error) {
      adminError =
        error instanceof ApiError ? error.message : "Administration data could not be read.";
    }
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account, and — with the admin scope — the accounts and credentials of others."
      />

      <div className="space-y-4">
        <section className="panel" aria-labelledby="account-heading">
          <div className="border-b border-line px-4 py-3">
            <h2 id="account-heading" className="text-sm font-semibold text-ink">
              Your account
            </h2>
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-4 py-3 sm:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-faint">Signed in as</dt>
              <dd className="text-sm text-ink">{identity.subject}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Kind</dt>
              <dd className="text-sm text-ink">{identity.kind}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Session expires</dt>
              <dd className="identifier">
                {identity.expires_at === null ? "never" : formatTime(identity.expires_at)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Credential</dt>
              <dd className="identifier">{shortId(identity.token_uuid)}</dd>
            </div>
            <div className="col-span-2 sm:col-span-4">
              <dt className="mb-1 text-xs text-ink-faint">Scopes</dt>
              <dd className="flex flex-wrap gap-1.5">
                {identity.scopes.map((scope) => (
                  <StatusBadge key={scope} tone="info">
                    {scope}
                  </StatusBadge>
                ))}
              </dd>
            </div>
          </dl>
          <div className="border-t border-line p-4">
            <h3 className="mb-3 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
              Change password
            </h3>
            <PasswordForm />
          </div>
        </section>

        {isAdmin ? (
          adminError !== null ? (
            <ErrorState title="Administration unavailable" message={adminError} />
          ) : (
            <>
              <AccountsPanel accounts={accounts} currentSubject={identity.subject} />
              <TokensPanel tokens={tokens} />
            </>
          )
        ) : (
          <p className="panel px-4 py-6 text-sm text-ink-muted">
            Account and credential administration needs the{" "}
            <span className="identifier">admin</span> scope.
          </p>
        )}
      </div>
    </>
  );
}
