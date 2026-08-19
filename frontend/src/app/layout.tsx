import Link from "next/link";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { fetchIdentity } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Face ID review",
  description: "Review queue for identity proposals awaiting a human decision.",
  // Biometric review pages have no business in search results.
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Decisions are attributed to whoever is signed in, so a reviewer should
  // never be in doubt about which identity they are acting under.
  const identity = await fetchIdentity().catch(() => null);

  return (
    <html lang="en">
      <body>
        <header className="masthead">
          <Link href="/" className="wordmark">
            Face&nbsp;ID <span>review</span>
          </Link>
          {identity === null ? null : (
            <div className="whoami">
              <span className="subject">
                signed in as <strong>{identity.subject}</strong>
              </span>
              <form action="/sign-out" method="post">
                <button type="submit" className="subtle">
                  Sign out
                </button>
              </form>
            </div>
          )}
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
