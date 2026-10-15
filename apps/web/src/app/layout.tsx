import type { Metadata, Viewport } from "next";
import { DashboardShell } from "./_shell/dashboard-shell";
import "./globals.css";

import React from "react";
export const metadata: Metadata = {
  title: "NetMind — Agentic control for SDN",
  description:
    "Network operations dashboard for a trustworthy agentic SDN lab: live topology, telemetry, streamed agent activity and an auditable recovery record.",
  applicationName: "NetMind",
};

export const viewport: Viewport = {
  themeColor: "#f5f6f8",
  colorScheme: "light",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <DashboardShell>{children}</DashboardShell>
      </body>
    </html>
  );
}
