import Link from "next/link";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Face ID review",
  description: "Review queue for identity proposals awaiting a human decision.",
  // Biometric review pages have no business in search results or history-based
  // previews.
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="masthead">
          <Link href="/" className="wordmark">
            Face&nbsp;ID <span>review</span>
          </Link>
          <form action="/sign-out" method="post">
            <button type="submit">Sign out</button>
          </form>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
