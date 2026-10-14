/** Small formatting helpers. The UI formats measured values; it never invents them. */

export function formatMs(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)} ms`;
}

export function formatPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function formatMbps(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(0)} Mb/s`;
}

export function formatCount(value: number | null | undefined, singular: string, plural = `${singular}s`): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value} ${value === 1 ? singular : plural}`;
}

/** Local clock time only; the full ISO stamp stays available in the title. */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

/** Truncate a forwarding path for table cells: `h1 → s1 → s2 → s4 → h4`. */
export function formatPath(path: string[] | undefined): string {
  if (!path || path.length === 0) return "—";
  return path.join(" → ");
}
