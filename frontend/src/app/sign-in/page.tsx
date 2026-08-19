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
    store.set(SESSION_COOKIE, token, cookieOptions(process.env.NODE_ENV === "production"));
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

  return (
    <div className="signin">
      <div>
        <h1>Sign in</h1>
        <p className="lede" style={{ marginBottom: 0 }}>
          Decisions you record are attributed to your account in an append-only audit log.
        </p>
      </div>

      {message === null ? null : <p className="notice error">{message}</p>}

      <form className="review card padded" action={submit}>
        <label>
          Email
          <input
            type="email"
            name="email"
            autoComplete="username"
            placeholder="you@example.com"
            autoFocus
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
          />
        </label>
        <div className="actions">
          <button type="submit" className="primary">
            Sign in
          </button>
        </div>
      </form>

      <p className="notice">
        Accounts are created by an operator with{" "}
        <span className="mono">python -m app.users create</span>.
      </p>
    </div>
  );
}
