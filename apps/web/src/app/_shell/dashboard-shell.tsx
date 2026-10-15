"use client";

import React from "react";
import type { ReactNode } from "react";
import { LabDataProvider } from "./lab-data";
import { Rail } from "./rail";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { ShellFooter } from "./panels";

/**
 * The application shell: top bar, left rail, the dashboard column, the
 * assistant sidebar and the footer. Layout and metric values come from
 * `globals.css` plus the design tokens in `apps/web/types.ts` documentation.
 *
 * The whole shell sits inside `LabDataProvider` so the top bar, rail, metric
 * strip and footer all share one polling loop rather than four.
 */
export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <LabDataProvider>
      <div className="app-shell">
        <Topbar />
        <Rail />
        <main className="app-main" id="region-top">
          {children}
        </main>
        <Sidebar />
        <footer className="app-footbar">
          <ShellFooter />
        </footer>
      </div>
    </LabDataProvider>
  );
}
