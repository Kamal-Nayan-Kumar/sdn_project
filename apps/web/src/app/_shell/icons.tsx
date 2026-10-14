import type { SVGProps } from "react";

/**
 * Inline SVG icon set. 24x24 grid, 1.5 stroke, `currentColor` — no icon font,
 * no emoji, no raster images.
 */

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Project mark: a selected path, a dashed alternative, and the agent vertex. */
export function NetMindMark({ size = 22, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path
        d="M4.5 18 L12 6.2 L19.5 18"
        stroke="var(--blue)"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M4.5 18 C 9 21.4, 15 21.4, 19.5 18"
        stroke="var(--cyan)"
        strokeWidth={1.4}
        strokeLinecap="round"
        strokeDasharray="2.6 2.4"
      />
      <circle cx="4.5" cy="18" r="2.3" fill="var(--surface)" stroke="var(--ink)" strokeWidth={1.4} />
      <circle cx="19.5" cy="18" r="2.3" fill="var(--surface)" stroke="var(--ink)" strokeWidth={1.4} />
      <path
        d="M12 3.4 L15.1 6.5 L12 9.6 L8.9 6.5 Z"
        fill="var(--blue)"
        stroke="var(--blue)"
        strokeWidth={1.2}
        strokeLinejoin="round"
      />
    </svg>
  );
}

export const IconDashboard = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
  </Icon>
);

export const IconChat = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20.5 11.6a8 8 0 0 1-8 8 8.4 8.4 0 0 1-3.6-.8L4 21l1.5-4.4A8 8 0 1 1 20.5 11.6Z" />
  </Icon>
);

export const IconTopology = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="18" cy="5.2" r="2.6" />
    <circle cx="6" cy="12" r="2.6" />
    <circle cx="18" cy="18.8" r="2.6" />
    <path d="M8.5 13.3 15.6 18M15.6 6 8.5 10.7" />
  </Icon>
);

export const IconIntent = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 21.5S19.5 17.6 19.5 12V5.4L12 2.5 4.5 5.4V12c0 5.6 7.5 9.5 7.5 9.5Z" />
    <path d="M8.8 11.9 11 14.1l4-4.2" />
  </Icon>
);

export const IconHistory = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.6" />
    <path d="M12 6.8V12l3.4 2" />
  </Icon>
);

export const IconAudit = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8.5 6H20M8.5 12H20M8.5 18H20" />
    <path d="M3.6 6h.01M3.6 12h.01M3.6 18h.01" />
  </Icon>
);

export const IconReset = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3.5 12a8.5 8.5 0 1 0 2.9-6.4L3.5 8.3" />
    <path d="M3.5 3.2v5.2h5.2" />
  </Icon>
);

/** Congestion: a signal ramp that breaks. */
export const IconCongestion = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 15.5 7.5 9l3.5 4.5L14 9" />
    <path d="M16.5 13.5 21 8.5" strokeDasharray="2.6 2.4" />
    <path d="M3 20h18" />
  </Icon>
);

export const IconMonitor = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6.5 4.5 12 10.5 17.5 4.5" />
    <path d="M3 19.5h18" />
  </Icon>
);

export const IconExpand = (p: IconProps) => (
  <Icon {...p}>
    <path d="M14 4h6v6M20 4l-7.5 7.5M10 20H4v-6M4 20l7.5-7.5" />
  </Icon>
);

export const IconInfo = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.6" />
    <path d="M12 11v5.5M12 7.8h.01" />
  </Icon>
);

export const IconLink = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9.5 14.5 14.5 9.5" />
    <path d="M11 6.6 12.9 4.7a3.6 3.6 0 0 1 5.1 5.1L16 11.9" />
    <path d="M13 17.4l-1.9 1.9a3.6 3.6 0 0 1-5.1-5.1L8 12.1" />
  </Icon>
);

export const IconFlow = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 7h13M3 12h9M3 17h13" />
    <path d="M17.5 9.5 21 12l-3.5 2.5" />
  </Icon>
);
