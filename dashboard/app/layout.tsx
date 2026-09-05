import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Sidebar } from "@/components/Sidebar";

import "./globals.css";

export const metadata: Metadata = {
  title: "Ripcord — Feature Flags",
  description: "A self-hostable feature-flag & gradual-rollout dashboard.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex-1">
            <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
