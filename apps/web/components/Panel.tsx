import React from "react";
/**
 * `Panel` -- the shared card every dashboard section sits in.
 *
 * One surface style for the whole app: thin neutral border, modest radius,
 * a small uppercase title, an optional action slot and an optional footer.
 * `collapsible` keeps the header keyboard-operable with real ARIA state rather
 * than a click-only div.
 */

"use client";

import { useId, useState, type CSSProperties, type ReactNode } from "react";
import { FONT_SANS, PALETTE, RADIUS, cx, css, focusRing, useHoverFocus } from "../lib/ui";

export type PanelTone = "neutral" | "info" | "ok" | "warn" | "danger";

export interface PanelProps {
  /** Panel heading. */
  title: ReactNode;
  /** Secondary line under the heading. */
  subtitle?: ReactNode;
  /** Right-hand controls (buttons, badges, ...). */
  actions?: ReactNode;
  /** Small pill next to the heading (a count, a status). */
  badge?: ReactNode;
  children?: ReactNode;
  /** Bottom strip for totals, timestamps, footnotes. */
  footer?: ReactNode;
  /** Accent colour for the heading rule. */
  tone?: PanelTone;
  /** Make the body collapsible (the header stays visible). */
  collapsible?: boolean;
  /** Initial expanded state when `collapsible`. Default `true`. */
  defaultOpen?: boolean;
  /** Heading level for the section, so the page outline stays correct. */
  headingLevel?: 2 | 3 | 4;
  id?: string;
  className?: string;
  style?: CSSProperties;
}

const TONE_COLOR: Record<PanelTone, string> = {
  neutral: PALETTE.border,
  info: PALETTE.blue,
  ok: PALETTE.ok,
  warn: PALETTE.warn,
  danger: PALETTE.danger,
};

const PANEL_STYLE: CSSProperties = {
  background: PALETTE.panel,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: RADIUS.lg,
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
  overflow: "hidden",
  fontFamily: FONT_SANS,
  color: PALETTE.ink,
};

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "10px",
  padding: "10px 14px",
  borderBottom: `1px solid ${PALETTE.border}`,
};

export function Panel({
  title,
  subtitle,
  actions,
  badge,
  children,
  footer,
  tone = "neutral",
  collapsible = false,
  defaultOpen = true,
  headingLevel = 2,
  id,
  className,
  style,
}: PanelProps) {
  const generatedId = useId();
  const panelId = id ?? generatedId;
  const bodyId = `${panelId}-body`;
  const [open, setOpen] = useState(defaultOpen);
  const { hovered, focused, handlers } = useHoverFocus();
  const Heading = headingLevel === 2 ? "h2" : headingLevel === 3 ? "h3" : "h4";

  const label = (
    <span style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
      <span
        aria-hidden="true"
        style={{
          width: "3px",
          alignSelf: "stretch",
          minHeight: "18px",
          borderRadius: "2px",
          background: TONE_COLOR[tone],
        }}
      />
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: "12px", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase" }}>
          {title}
        </span>
        {subtitle ? (
          <span style={{ fontSize: "11px", color: PALETTE.muted, marginTop: "2px" }}>{subtitle}</span>
        ) : null}
      </span>
      {badge}
    </span>
  );

  return (
    <section
      id={panelId}
      className={cx("sdn-panel", className)}
      style={css(PANEL_STYLE, style)}
      aria-labelledby={`${panelId}-title`}
      data-collapsed={collapsible && !open ? "true" : "false"}
    >
      <div style={css(HEADER_STYLE, hovered && { background: "rgba(37, 99, 235, 0.03)" })}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {collapsible ? (
            <Heading id={`${panelId}-title`} style={{ margin: 0, font: "inherit", fontSize: "12px" }}>
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                aria-controls={bodyId}
                {...handlers}
                style={css(
                  {
                    appearance: "none",
                    background: "transparent",
                    border: "none",
                    padding: 0,
                    margin: 0,
                    font: "inherit",
                    color: "inherit",
                    textAlign: "left",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    width: "100%",
                  },
                  focusRing(focused),
                )}
              >
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-block",
                    transform: open ? "rotate(90deg)" : "rotate(0deg)",
                    transition: "transform 120ms ease",
                    color: PALETTE.muted,
                    fontSize: "10px",
                  }}
                >
                  ▶
                </span>
                {label}
              </button>
            </Heading>
          ) : (
            <Heading id={`${panelId}-title`} style={{ margin: 0, font: "inherit", fontSize: "12px" }}>
              {label}
            </Heading>
          )}
        </div>
        {actions ? (
          <div style={{ display: "flex", alignItems: "center", gap: "6px", flexShrink: 0 }}>{actions}</div>
        ) : null}
      </div>

      {(!collapsible || open) && children ? (
        <div id={bodyId} style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: "10px" }}>
          {children}
        </div>
      ) : null}

      {(!collapsible || open) && footer ? (
        <div
          style={{
            padding: "8px 14px",
            borderTop: `1px solid ${PALETTE.border}`,
            fontSize: "11px",
            color: PALETTE.muted,
            display: "flex",
            justifyContent: "space-between",
            gap: "8px",
          }}
        >
          {footer}
        </div>
      ) : null}
    </section>
  );
}

export default Panel;
