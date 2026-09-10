import type { Metadata } from "next";
import type { ReactNode } from "react";
import { ApplicationShell } from "@/components/shell/application-shell";
import { Header } from "@/components/shell/header";
import { Sidebar } from "@/components/shell/sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hawkeye — Person Intelligence",
  description: "Hawkeye: face identification, enrolment and review workstation.",
  // A biometric workstation has no business in search results.
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-bg text-ink antialiased">
        <ApplicationShell header={<Header />} sidebar={<Sidebar />}>
          {children}
        </ApplicationShell>
      </body>
    </html>
  );
}
