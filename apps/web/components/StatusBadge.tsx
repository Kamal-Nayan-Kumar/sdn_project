import React from "react";
/**
 * `StatusBadge` -- the single status visual used everywhere.
 *
 * Status is never conveyed by colour alone: each badge carries its text, and
 * optionally a glyph and a `title` for the full explanation. Tones map onto the
 * spec's palette (green = verified, amber = uncertain, red = violation, grey =
 * unknown).
 */

"use client";

import type { CSSProperties, ReactNode } from "react";
import { FONT_SANS, RADIUS, TONE_BG, TONE_COLOR, cx, type Tone } from "../lib/ui";

export interface StatusBadgeProps {
  /** Text shown in the badge. Never rely on the colour to carry it. */
  label: ReactNode;
  tone?: Tone;
  size?: "sm" | "md";
  /** Glyph before the label (a dot, an icon). */
  icon?: ReactNode;
  /** Full explanation; becomes the `title` and an `aria-label` when different. */
  title?: string;
  /** Announce changes politely (`role="status"`). */
  live?: boolean;
  /** Draw the leading dot that implies "live state". */
  dot?: boolean;
  className?: string;
  style?: CSSProperties;
  testId?: string;
}

export function StatusBadge({
  label,
  tone = "neutral",
  size = "sm",
  icon,
  title,
  live = false,
  dot = false,
  className,
  style,
  testId,
}: StatusBadgeProps) {
  const color = TONE_COLOR[tone];
  const text = typeof label === "string" ? label : undefined;
  return (
    <span
      className={cx("sdn-status-badge", className)}
      data-tone={tone}
      data-testid={testId}
      role={live ? "status" : undefined}
      aria-live={live ? "polite" : undefined}
      aria-label={title && text && title !== text ? title : undefined}
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "5px",
        padding: size === "sm" ? "2px 7px" : "4px 9px",
        fontSize: size === "sm" ? "10.5px" : "12px",
        fontWeight: 600,
        lineHeight: 1.35,
        fontFamily: FONT_SANS,
        letterSpacing: "0.02em",
        color,
        background: TONE_BG[tone],
        border: `1px solid ${color}33`,
        borderRadius: RADIUS.pill,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {dot ? (
        <span
          aria-hidden="true"
          style={{
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            background: color,
            flexShrink: 0,
          }}
        />
      ) : null}
      {icon}
      <span>{label}</span>
    </span>
  );
}

export default StatusBadge;
