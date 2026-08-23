import type { Metadata } from "next";
import type { ReactNode } from "react";
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
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <Header />
            <main className="mx-auto w-full max-w-[100rem] flex-1 p-4 lg:p-6">
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
