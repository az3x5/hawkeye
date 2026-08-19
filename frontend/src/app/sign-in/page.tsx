import { redirect } from "next/navigation";
import { cookieOptions, SESSION_COOKIE } from "@/lib/session";
import { cookies } from "next/headers";

export const dynamic = "force-dynamic";

/**
 * Sign-in.
 *
 * Tokens are issued out of band by an operator (`python -m app.tokens issue`)
 * and pasted here. There is deliberately no self-service registration: an
 * application that can mint its own credentials can escalate its own
 * privileges.
 */
export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  async function signIn(formData: FormData) {
    "use server";
    const token = String(formData.get("token") ?? "").trim();
    if (token === "") redirect("/sign-in?error=empty");

    const store = await cookies();
    store.set(SESSION_COOKIE, token, cookieOptions(process.env.NODE_ENV === "production"));
    redirect("/");
  }

  return (
    <>
      <h1>Sign in</h1>
      <p className="lede">
        Your review token identifies you in the audit log. Every decision you record is
        attributed to it and cannot be changed afterwards.
      </p>

      {error === undefined ? null : (
        <p className="notice error">
          {error === "empty" ? "Enter your token to continue." : "That token was not accepted."}
        </p>
      )}

      <form className="review card" action={signIn}>
        <label>
          Review token
          <input
            type="password"
            name="token"
            autoComplete="off"
            placeholder="faceid_…"
            required
          />
        </label>
        <div className="actions">
          <button type="submit" className="primary">
            Sign in
          </button>
        </div>
        <p className="notice">
          Ask an operator to issue you one. Tokens are shown once when created and are not
          recoverable.
        </p>
      </form>
    </>
  );
}
