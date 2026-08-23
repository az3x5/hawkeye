import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { ApiError, signIn } from "@/lib/api";
import { SESSION_COOKIE, cookieOptions } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Sign-in.
 *
 * The password is exchanged for a short-lived session credential server-side;
 * the credential is kept in an httpOnly cookie and the password itself never
 * reaches the browser's storage or this app's logs.
 */
export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  async function submit(formData: FormData) {
    "use server";
    const email = String(formData.get("email") ?? "").trim();
    const password = String(formData.get("password") ?? "");
    if (email === "" || password === "") redirect("/sign-in?error=missing");

    let token: string;
    try {
      ({ token } = await signIn(email, password));
    } catch (caught) {
      // Deliberately one message for every failure: which half was wrong is
      // not something an unauthenticated caller has earned.
      if (caught instanceof ApiError && caught.status === 429) {
        redirect("/sign-in?error=throttled");
      }
      redirect("/sign-in?error=rejected");
    }

    const store = await cookies();
    store.set(SESSION_COOKIE, token, cookieOptions());
    redirect("/");
  }

  const message =
    error === "missing"
      ? "Enter your email and password."
      : error === "throttled"
        ? "Too many attempts. Wait a minute and try again."
        : error === "rejected"
          ? "Email or password is incorrect."
          : null;

  const field =
    "w-full rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink";

  return (
    <div className="mx-auto max-w-md space-y-4 py-10">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Sign in</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Decisions you record are attributed to your account in an append-only audit log.
        </p>
      </div>

      {message === null ? null : (
        <p className="rounded-md border-l-2 border-reject bg-reject/10 px-3 py-2 text-sm text-reject">
          {message}
        </p>
      )}

      <form className="panel space-y-3 p-4" action={submit}>
        <label className="block space-y-1.5 text-sm text-ink-muted">
          Email
          <input
            type="email"
            name="email"
            autoComplete="username"
            placeholder="you@example.com"
            className={field}
            autoFocus
            required
          />
        </label>
        <label className="block space-y-1.5 text-sm text-ink-muted">
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            className={field}
            required
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg"
        >
          Sign in
        </button>
      </form>

      <p className="rounded-md border-l-2 border-line-strong bg-surface-raised px-3 py-2 text-sm text-ink-muted">
        Accounts are created by an operator with{" "}
        <span className="identifier">python -m app.users create</span>.
      </p>
    </div>
  );
}
