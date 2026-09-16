"use client";

import { Copy, KeyRound, KeySquare } from "lucide-react";
import { useActionState, useState, useTransition } from "react";
import {
  issueTokenAction,
  revokeTokenAction,
  type ActionResult,
  type IssueResult,
} from "@/app/settings/actions";
import { ScopePicker } from "@/app/settings/scope-picker";
import { StatusBadge } from "@/components/states/status-badge";
import { formatTime, shortId } from "@/lib/format";
import type { TokenRecord } from "@/lib/types";

const FIELD = "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink";
const BLACKGLASS_SCOPES = ["language", "media:write", "media:read"] as const;

/** Service credentials: what exists, issuing more, and revoking them. */
export function TokensPanel({ tokens }: { tokens: TokenRecord[] }) {
  const [result, action, pending] = useActionState<IssueResult | null, FormData>(
    issueTokenAction,
    null,
  );
  const [notice, setNotice] = useState<ActionResult | null>(null);
  const [busy, startTransition] = useTransition();
  const [issuing, setIssuing] = useState(false);

  function revoke(token: TokenRecord) {
    startTransition(async () => {
      setNotice(await revokeTokenAction(token.token_uuid));
    });
  }

  return (
    <section className="panel" aria-labelledby="tokens-heading">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 id="tokens-heading" className="text-sm font-semibold text-ink">
          Service credentials
        </h2>
        <button
          type="button"
          onClick={() => setIssuing((open) => !open)}
          className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-sm text-ink-muted hover:text-ink"
        >
          <KeySquare className="size-4" aria-hidden="true" />
          {issuing ? "Cancel" : "Issue credential"}
        </button>
      </div>

      <div className="border-b border-line p-4">
        <div className="rounded-lg border border-accent/30 bg-accent/5 p-4">
          <div className="flex items-start gap-3">
            <span className="rounded-md bg-accent/10 p-2 text-accent">
              <KeyRound className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-semibold text-ink">BlackGlass bearer token</h3>
              <p className="mt-1 text-sm text-ink-muted">
                Create a 90-day service credential for ingestion and result retrieval. The
                credential cannot manage accounts, enrol faces, or run identification.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Granted scopes">
                {BLACKGLASS_SCOPES.map((scope) => (
                  <StatusBadge key={scope} tone="info">
                    {scope}
                  </StatusBadge>
                ))}
              </div>

              <form action={action} className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
                <label className="block flex-1 space-y-1.5 text-sm text-ink-muted">
                  Credential name
                  <input
                    type="text"
                    name="subject"
                    defaultValue="blackglass-production"
                    className={FIELD}
                    required
                  />
                </label>
                <input type="hidden" name="kind" value="service" />
                <input type="hidden" name="expires_in_days" value="90" />
                {BLACKGLASS_SCOPES.map((scope) => (
                  <input key={scope} type="hidden" name="scopes" value={scope} />
                ))}
                <button
                  type="submit"
                  disabled={pending}
                  className="shrink-0 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
                >
                  {pending ? "Creating…" : "Create bearer token"}
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>

      {result !== null && !result.ok ? (
        <p
          className="border-b border-line border-l-2 border-l-reject bg-reject/10 px-4 py-2 text-sm text-reject"
          role="alert"
        >
          {result.message}
        </p>
      ) : null}

      {issuing ? (
        <form action={action} className="max-w-sm space-y-3 border-b border-line p-4">
          <label className="block space-y-1.5 text-sm text-ink-muted">
            Subject
            <input
              type="text"
              name="subject"
              placeholder="ingest-service"
              className={FIELD}
              required
            />
          </label>
          <label className="block space-y-1.5 text-sm text-ink-muted">
            Kind
            <select name="kind" className={FIELD} defaultValue="service">
              <option value="service">service</option>
              <option value="user">user</option>
            </select>
          </label>
          <ScopePicker />
          <label className="block space-y-1.5 text-sm text-ink-muted">
            Expires in days
            <input
              type="number"
              name="expires_in_days"
              min={1}
              max={3650}
              placeholder="defaults to the configured lifetime"
              className={FIELD}
            />
          </label>
          <label className="flex items-start gap-2 text-sm text-ink-muted">
            <input
              type="checkbox"
              name="never_expires"
              className="mt-0.5 size-4 rounded border-line bg-surface-sunken"
            />
            <span>
              Never expires
              <span className="block text-xs text-ink-faint">
                Stays valid until somebody notices it has leaked. Ask for this deliberately.
              </span>
            </span>
          </label>

          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {pending ? "Issuing…" : "Issue credential"}
          </button>
        </form>
      ) : null}

      {result !== null && result.ok ? <SecretOnce secret={result.token.token} /> : null}

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
              <th scope="col">Subject</th>
              <th scope="col">Kind</th>
              <th scope="col">Scopes</th>
              <th scope="col">Expires</th>
              <th scope="col">State</th>
              <th scope="col" className="text-right">
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            {tokens.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center text-ink-muted">
                  No credentials have been issued.
                </td>
              </tr>
            ) : (
              tokens.map((token) => (
                <tr key={token.token_uuid}>
                  <td className="text-ink">
                    {token.subject}
                    <span className="identifier ml-2">{shortId(token.token_uuid)}</span>
                  </td>
                  <td className="text-ink-muted">{token.kind}</td>
                  <td className="identifier">{token.scopes.join(", ")}</td>
                  <td className="text-ink-muted">
                    {token.expires_at === null ? "never" : formatTime(token.expires_at)}
                  </td>
                  <td>
                    <StatusBadge
                      tone={token.expired ? "review" : token.active ? "accept" : "neutral"}
                    >
                      {token.expired ? "expired" : token.active ? "active" : "revoked"}
                    </StatusBadge>
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      onClick={() => revoke(token)}
                      disabled={busy || !token.active}
                      className="rounded-md border border-line px-2.5 py-1 text-xs text-ink-muted hover:text-ink disabled:opacity-40"
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * The one time a secret is visible.
 *
 * Only its hash is stored, so this is genuinely the only chance to copy it.
 * Held in component state and never written to browser storage.
 */
function SecretOnce({ secret }: { secret: string }) {
  const [copied, setCopied] = useState<"token" | "header" | null>(null);
  const authorizationHeader = `Authorization: Bearer ${secret}`;

  function copy(value: string, kind: "token" | "header") {
    void navigator.clipboard.writeText(value).then(() => setCopied(kind));
  }

  return (
    <div className="border-b border-line bg-accept/5 p-4">
      <p className="text-sm font-semibold text-ink">Credential issued</p>
      <p className="mt-1 text-sm text-ink-muted">
        Copy it now. Only its hash is stored, so it cannot be shown again.
      </p>
      <div className="mt-3 grid gap-3">
        <SecretValue
          label="Bearer token"
          value={secret}
          copied={copied === "token"}
          onCopy={() => copy(secret, "token")}
        />
        <SecretValue
          label="Authorization header"
          value={authorizationHeader}
          copied={copied === "header"}
          onCopy={() => copy(authorizationHeader, "header")}
        />
      </div>
    </div>
  );
}

function SecretValue({
  label,
  value,
  copied,
  onCopy,
}: {
  label: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-ink-faint">{label}</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="identifier min-w-0 flex-1 rounded border border-line bg-surface-sunken px-2 py-1.5 break-all text-ink">
          {value}
        </code>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-muted hover:text-ink"
          aria-label={`Copy ${label.toLowerCase()}`}
        >
          <Copy className="size-3.5" aria-hidden="true" />
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}
