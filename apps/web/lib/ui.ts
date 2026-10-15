/**
 * Shared visual primitives for the dashboard.
 *
 * Colours come from the CSS custom properties defined in `app/globals.css`
 * (`--blue`, `--cyan`, `--ink`, `--muted`, `--grid-line`, `--danger`, `--warn`,
 * `--ok`, ...) with the spec's palette as the fallback, so a component still
 * renders sensibly if the stylesheet has not loaded yet.
 *
 * Everything here is plain data + one tiny hook. No component file imports this
 * for behaviour, only for consistent colour, radius and focus handling.
 */

import { useCallback, useState } from "react";
import type { CSSProperties } from "react";

export const PALETTE = {
  blue: "var(--blue, #2563EB)",
  cyan: "var(--cyan, #0891B2)",
  ink: "var(--ink, #111827)",
  muted: "var(--muted, #6B7280)",
  gridLine: "var(--grid-line, #E2E8F0)",
  danger: "var(--danger, #DC2626)",
  warn: "var(--warn, #D97706)",
  ok: "var(--ok, #16A34A)",
  surface: "var(--surface, #FFFFFF)",
  canvas: "var(--canvas, #F8FAFC)",
  border: "var(--border, #E2E8F0)",
  panel: "var(--panel, #FFFFFF)",
} as const;

export type Tone = "neutral" | "info" | "ok" | "warn" | "danger" | "pending" | "running" | "cyan";

export const TONE_COLOR: Readonly<Record<Tone, string>> = {
  neutral: PALETTE.muted,
  info: PALETTE.blue,
  ok: PALETTE.ok,
  warn: PALETTE.warn,
  danger: PALETTE.danger,
  pending: PALETTE.muted,
  running: PALETTE.blue,
  cyan: PALETTE.cyan,
};

/** Background of a badge/pill: 12% of the tone over the surface. */
export const TONE_BG: Readonly<Record<Tone, string>> = {
  neutral: "rgba(107, 114, 128, 0.12)",
  info: "rgba(37, 99, 235, 0.10)",
  ok: "rgba(22, 163, 74, 0.12)",
  warn: "rgba(217, 119, 6, 0.14)",
  danger: "rgba(220, 38, 38, 0.10)",
  pending: "rgba(107, 114, 128, 0.10)",
  running: "rgba(37, 99, 235, 0.10)",
  cyan: "rgba(8, 145, 178, 0.12)",
};

export const FONT_SANS =
  '"Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
export const FONT_MONO =
  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';

export const RADIUS = {
  sm: "4px",
  md: "6px",
  lg: "10px",
  pill: "999px",
} as const;

/** Join class names, dropping falsy entries. */
export function cx(...parts: readonly (string | false | null | undefined)[]): string {
  return parts.filter((p): p is string => typeof p === "string" && p.length > 0).join(" ");
}

/** Shallow-merge style objects, dropping falsy ones. */
export function css(...styles: readonly (CSSProperties | false | null | undefined)[]): CSSProperties {
  return Object.assign({}, ...styles.filter((s): s is CSSProperties => Boolean(s))) as CSSProperties;
}

/** The focus ring for keyboard users. Never rely on colour alone. */
export function focusRing(focused: boolean): CSSProperties {
  return focused
    ? { outline: `2px solid ${PALETTE.blue}`, outlineOffset: "2px" }
    : { outline: "none" };
}

export interface HoverFocusState {
  hovered: boolean;
  focused: boolean;
  handlers: {
    readonly onMouseEnter: () => void;
    readonly onMouseLeave: () => void;
    readonly onFocus: () => void;
    readonly onBlur: () => void;
  };
}

/**
 * Hover/focus tracking for interactive elements. React state is used instead of
 * CSS pseudo-classes because these components carry their styling inline (so
 * the app's stylesheet can restyle structure, never behaviour).
 */
export function useHoverFocus(): HoverFocusState {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const handlers = {
    onMouseEnter: useCallback(() => setHovered(true), []),
    onMouseLeave: useCallback(() => setHovered(false), []),
    onFocus: useCallback(() => setFocused(true), []),
    onBlur: useCallback(() => setFocused(false), []),
  };
  return { hovered, focused, handlers };
}

const BUTTON_BASE: CSSProperties = {
  font: "inherit",
  fontSize: "12px",
  fontWeight: 500,
  lineHeight: 1.2,
  padding: "6px 10px",
  borderRadius: RADIUS.md,
  border: `1px solid ${PALETTE.border}`,
  background: PALETTE.surface,
  color: PALETTE.ink,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: "6px",
};

/** Props for the shared `<button>` look. */
export function buttonStyle(tone: Tone = "neutral", hovered = false, focused = false): CSSProperties {
  const color = TONE_COLOR[tone];
  return css(BUTTON_BASE, hovered && { background: TONE_BG[tone] }, focusRing(focused));
}

/** Format a number for display without pretending precision it does not have. */
export function fmt(
  value: number | null | undefined,
  digits = 1,
  fallback = "—",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  const rounded = Number(value.toFixed(digits));
  return String(rounded);
}

/** Signed delta, e.g. `-58.4`. */
export function fmtDelta(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const rounded = Number(value.toFixed(digits));
  return rounded > 0 ? `+${rounded}` : String(rounded);
}

/** Percentage with one decimal, always carrying the unit. */
export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${fmt(value, digits)}%`;
}

/** Pick a tone for a delta given whether lower is better. */
export function deltaTone(delta: number | null | undefined, lowerIsBetter = true): Tone {
  if (delta === null || delta === undefined || !Number.isFinite(delta) || delta === 0) return "neutral";
  const improving = lowerIsBetter ? delta < 0 : delta > 0;
  return improving ? "ok" : "danger";
}

/** Style for a thin scrollbar area; also used as the sidebar body. */
export const SCROLL_AREA: CSSProperties = {
  overflowY: "auto",
  scrollbarWidth: "thin",
  minHeight: 0,
};

/** Accessible visually-hidden text. */
export const SR_ONLY: CSSProperties = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: 0,
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};
