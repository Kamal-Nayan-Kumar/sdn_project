import React from "react";
/**
 * `TopologyView` -- the live network, drawn from actual state.
 *
 * Rules this component follows (they are the point of the panel):
 *   * geometry comes from `layoutTopology()`, which is deterministic: the same
 *     topology always draws the same picture;
 *   * `snapshot.links` is normalised with `linksArray()` because the lab serves
 *     it as an object keyed by `link_id` while FastAPI serves an array;
 *   * utilisation is encoded twice (line width *and* a numeric label) so it is
 *     readable, not just decorative;
 *   * the current path and the alternative path are visually distinct
 *     (solid blue vs dashed cyan) and both carry direction arrows;
 *   * nothing is invented: a link with no measurement gets no number, a flow
 *     with no measurement is listed as unknown.
 */

"use client";

import { useId, useMemo, type CSSProperties } from "react";
import type {
  FlowTelemetry,
  LinkTelemetry,
  
  NetworkSnapshot,
  Topology,
  TopologyNode,
} from "../lib/types";
import {
  congestedLinkIds,
  layoutTopology,
  linksArray,
  pathToEdgeIds,
  pointAlong,
  type LaidOutLink,
  type LaidOutNode,
} from "../lib/derive";
import { FONT_MONO, FONT_SANS, PALETTE, cx, fmt } from "../lib/ui";

export interface TopologyViewProps {
  /** Static topology: nodes + links. Falls back to `snapshot.topology`. */
  topology?: Topology | null;
  /** Live telemetry. `links` may be an array *or* a dict keyed by link_id. */
  snapshot?: NetworkSnapshot | null;
  /** Current forwarding path as a node sequence, e.g. `[h1, s1, s2, s4, h4]`. */
  currentPath?: readonly string[] | null;
  /** Alternative path the agent considered / applied. */
  alternativePath?: readonly string[] | null;
  /** Extra link ids to highlight (e.g. the operator's selection). */
  highlightLinks?: readonly string[] | null;
  /** Congested link ids; extends `snapshot.congested_links`. */
  congestedLinks?: readonly string[] | null;
  /** Called when a node is activated (click or Enter/Space). */
  onSelectNode?: (nodeId: string) => void;
  /** Called when a link is activated (click or Enter/Space). */
  onSelectLink?: (linkId: string) => void;
  showLegend?: boolean;
  /** Show the measured-flow table under the diagram. Default `true`. */
  showFlows?: boolean;
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
  testId?: string;
}

const LINK_IDLE = "var(--grid-line, #CBD5E1)";
const LABEL_OFFSET = 11;

interface LinkView {
  readonly link: LaidOutLink;
  readonly telemetry: LinkTelemetry | null;
  readonly utilization: number;
  readonly congested: boolean;
  readonly down: boolean;
  readonly onCurrent: boolean;
  readonly onAlternative: boolean;
  readonly highlighted: boolean;
}


/** Route a path through laid-out nodes, dropping nodes the layout does not know. */
function routePath(path: readonly string[] | null | undefined, byId: Record<string, LaidOutNode>): LaidOutNode[] {
  if (!path) return [];
  const out: LaidOutNode[] = [];
  for (const id of path) {
    const node = byId[id];
    if (node) out.push(node);
  }
  return out;
}

function arrow(
  x: number,
  y: number,
  angle: number,
  size: number,
  color: string,
  key: string,
): React.JSX.Element {
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const tip = { x: x + cos * size, y: y + sin * size };
  const left = { x: x - cos * size * 0.4 - sin * size * 0.55, y: y - sin * size * 0.4 + cos * size * 0.55 };
  const right = { x: x - cos * size * 0.4 + sin * size * 0.55, y: y - sin * size * 0.4 - cos * size * 0.55 };
  return (
    <path
      key={key}
      d={`M ${tip.x.toFixed(2)} ${tip.y.toFixed(2)} L ${left.x.toFixed(2)} ${left.y.toFixed(2)} L ${right.x.toFixed(
        2,
      )} ${right.y.toFixed(2)} Z`}
      style={{ fill: color }}
    />
  );
}

function LinkLabel({ view }: { view: LinkView }): React.JSX.Element | null {
  const { link, utilization, congested, down } = view;
  const latency = link.delayMs;
  const showUtilization = utilization > 0 || view.onCurrent || view.onAlternative;
  const showLatency = latency > 0 && (view.onCurrent || view.onAlternative || showUtilization);
  if (!showUtilization && !showLatency) return null;

  const horizontal = Math.abs(link.x2 - link.x1) >= Math.abs(link.y2 - link.y1);
  const anchor = horizontal ? "middle" : "start";
  const x = link.midX + (horizontal ? 0 : LABEL_OFFSET);
  const y = link.midY + (horizontal ? -LABEL_OFFSET : 4);
  const utilColor = congested ? PALETTE.danger : utilization >= 70 ? PALETTE.warn : PALETTE.muted;

  return (
    <g pointerEvents="none">
      <rect
        x={horizontal ? x - 26 : x - 3}
        y={horizontal ? y - 16 : y - 8}
        width={horizontal ? 52 : 54}
        height={16}
        rx={3}
        style={{ fill: "var(--surface, #FFFFFF)", opacity: 0.88 }}
      />
      {showUtilization ? (
        <text
          x={x}
          y={y - 3}
          textAnchor={anchor}
          style={{ font: `600 9px ${FONT_MONO}`, fill: down ? PALETTE.danger : utilColor }}
        >
          {fmt(utilization, 0)}%
        </text>
      ) : null}
      {showLatency ? (
        <text
          x={x}
          y={y + 6}
          textAnchor={anchor}
          style={{ font: `9px ${FONT_MONO}`, fill: PALETTE.muted }}
        >
          {fmt(latency, 0)} ms{down ? " down" : ""}
        </text>
      ) : null}
    </g>
  );
}

export function TopologyView({
  topology,
  snapshot,
  currentPath,
  alternativePath,
  highlightLinks,
  congestedLinks,
  onSelectNode,
  onSelectLink,
  showLegend = true,
  showFlows = true,
  className,
  style,
  ariaLabel = "Network topology with current and alternative flow paths",
  testId = "topology-view",
}: TopologyViewProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const gridId = `grid-${uid}`;
  const gridMinorId = `grid-minor-${uid}`;

  const resolvedTopology: Topology | null = topology ?? snapshot?.topology ?? null;
  const nodes: readonly TopologyNode[] = resolvedTopology?.nodes ?? [];
  const links = useMemo(() => resolvedTopology?.links ?? [], [resolvedTopology]);

  const telemetry = useMemo(() => linksArray(snapshot), [snapshot]);
  const telemetryById = useMemo(() => {
    const out: Record<string, LinkTelemetry> = {};
    for (const link of telemetry) out[link.link_id] = link;
    return out;
  }, [telemetry]);

  const congested = useMemo(() => {
    const set = new Set(congestedLinkIds(snapshot));
    for (const id of congestedLinks ?? []) set.add(id);
    return set;
  }, [snapshot, congestedLinks]);

  const currentEdges = useMemo(() => new Set(pathToEdgeIds(currentPath)), [currentPath]);
  const alternativeEdges = useMemo(() => new Set(pathToEdgeIds(alternativePath)), [alternativePath]);
  const selectedEdges = useMemo(() => new Set(highlightLinks ?? []), [highlightLinks]);

  const layout = useMemo(
    () =>
      layoutTopology(nodes, links, {
        // Deterministic sizing; scaled to the container with `preserveAspectRatio`.
        columnGap: 150,
        rowGap: 104,
        margin: 72,
      }),
    [nodes, links],
  );

  const linkViews: LinkView[] = useMemo(
    () =>
      layout.links.map((link) => {
        const tel = telemetryById[link.id] ?? null;
        const util = tel ? Math.max(0, Math.min(100, tel.utilization_pct)) : 0;
        const status = tel?.status ?? link.status;
        const down = status === "down" || status === "degraded";
        return {
          link,
          telemetry: tel,
          utilization: util,
          congested: congested.has(link.id) || down,
          down,
          onCurrent: currentEdges.has(link.id),
          onAlternative: alternativeEdges.has(link.id) && !currentEdges.has(link.id),
          highlighted: selectedEdges.has(link.id),
        };
      }),
    [layout.links, telemetryById, congested, currentEdges, alternativeEdges, selectedEdges],
  );

  const currentRoute = useMemo(() => routePath(currentPath, layout.byId), [currentPath, layout.byId]);
  const alternativeRoute = useMemo(
    () => routePath(alternativePath, layout.byId),
    [alternativePath, layout.byId],
  );

  const flows: readonly FlowTelemetry[] = snapshot?.flows ?? [];

  const pathSummary = useMemo(() => {
    const format = (path: readonly string[] | null | undefined): string =>
      path && path.length > 1 ? pathToEdgeIds(path).join(" → ") : "unknown";
    return { current: format(currentPath), alternative: format(alternativePath) };
  }, [currentPath, alternativePath]);

  const label = `${ariaLabel}. ${layout.nodes.length} nodes, ${layout.links.length} links. Current path ${
    pathSummary.current
  }. Alternative path ${pathSummary.alternative}.`;

  const viewWidth = layout.width + 40;
  const viewHeight = layout.height + 30;

  return (
    <div
      className={cx("sdn-topology", className)}
      style={{ display: "flex", flexDirection: "column", gap: "8px", minWidth: 0, ...style }}
      data-testid={testId}
    >
      <svg
        role="img"
        aria-label={label}
        viewBox={`-20 -15 ${viewWidth} ${viewHeight}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        style={{ display: "block", background: "var(--canvas, #F8FAFC)", borderRadius: 8, maxHeight: "460px" }}
        data-testid="topology-svg"
      >
        <title>Network topology</title>
        <desc>{label}</desc>

        <defs>
          <pattern id={gridId} width="24" height="24" patternUnits="userSpaceOnUse">
            <path d="M 24 0 L 0 0 0 24" fill="none" style={{ stroke: LINK_IDLE }} strokeWidth="1" />
          </pattern>
          <pattern id={gridMinorId} width="6" height="6" patternUnits="userSpaceOnUse">
            <path d="M 6 0 L 0 0 0 6" fill="none" style={{ stroke: LINK_IDLE }} strokeWidth="0.5" opacity="0.6" />
          </pattern>
        </defs>

        <rect x={-20} y={-15} width={viewWidth} height={viewHeight} fill={`url(#${gridMinorId})`} />
        <rect x={-20} y={-15} width={viewWidth} height={viewHeight} fill={`url(#${gridId})`} />

        {/* --- links ------------------------------------------------------ */}
        <g>
          {linkViews.map((view) => {
            const { link } = view;
            const width = view.down ? 1.2 : 0.8 + (2.2 * view.utilization) / 100;
            const stroke = view.congested
              ? PALETTE.danger
              : view.down
                ? PALETTE.muted
                : view.utilization >= 70
                  ? PALETTE.warn
                  : LINK_IDLE;
            const interactive = typeof onSelectLink === "function";
            const title = `${link.source}–${link.target} (${link.id}): ${
              view.telemetry ? `${fmt(view.utilization, 1)}% utilised` : "no measurement"
            }, ${fmt(link.delayMs, 0)} ms${view.congested ? ", congested" : ""}${
              link.congestible ? ", can be congested" : ""
            }`;
            return (
              <g
                key={link.id}
                data-testid={`link-${link.id}`}
                data-congested={view.congested ? "true" : "false"}
                data-utilization={fmt(view.utilization, 1)}
                tabIndex={interactive ? 0 : undefined}
                role={interactive ? "button" : undefined}
                aria-label={interactive ? title : undefined}
                onClick={interactive ? () => onSelectLink?.(link.id) : undefined}
                onKeyDown={
                  interactive
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelectLink?.(link.id);
                        }
                      }
                    : undefined
                }
                style={{ cursor: interactive ? "pointer" : undefined, outline: "none" }}
              >
                <title>{title}</title>
                <line
                  x1={link.x1}
                  y1={link.y1}
                  x2={link.x2}
                  y2={link.y2}
                  strokeLinecap="round"
                  strokeDasharray={view.down ? "5 5" : view.congested ? "7 4" : undefined}
                  style={{ stroke, strokeWidth: width, opacity: view.down ? 0.7 : 1 }}
                />
                {view.congested ? (
                  <line
                    x1={link.x1}
                    y1={link.y1}
                    x2={link.x2}
                    y2={link.y2}
                    strokeLinecap="round"
                    style={{ stroke: PALETTE.danger, strokeWidth: width + 2, opacity: 0.25 }}
                  >
                    <animate
                      attributeName="opacity"
                      values="0.35;0.08;0.35"
                      dur="2.4s"
                      repeatCount="indefinite"
                    />
                  </line>
                ) : null}
                {view.highlighted ? (
                  <line
                    x1={link.x1}
                    y1={link.y1}
                    x2={link.x2}
                    y2={link.y2}
                    strokeLinecap="round"
                    style={{ stroke: PALETTE.ink, strokeWidth: 1, opacity: 0.45, strokeDasharray: "2 3" }}
                  />
                ) : null}
                <LinkLabel view={view} />
              </g>
            );
          })}
        </g>

        {/* --- current path (solid blue) --------------------------------- */}
        {currentRoute.length > 1 ? (
          <g pointerEvents="none" data-testid="path-current">
            <polyline
              points={currentRoute.map((n) => `${n.x},${n.y}`).join(" ")}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: "var(--surface, #FFFFFF)", strokeWidth: 7, opacity: 0.85 }}
            />
            <polyline
              points={currentRoute.map((n) => `${n.x},${n.y}`).join(" ")}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: PALETTE.blue, strokeWidth: 3 }}
            />
            {currentRoute.slice(0, -1).map((node, index) => {
              const next = currentRoute[index + 1];
              const mid = pointAlong(
                { x1: node.x, y1: node.y, x2: next.x, y2: next.y } as LaidOutLink,
                0.5,
              );
              const angle = Math.atan2(next.y - node.y, next.x - node.x);
              return arrow(mid.x, mid.y, angle, 7, PALETTE.blue, `cur-${node.id}-${next.id}`);
            })}
          </g>
        ) : null}

        {/* --- alternative path (dashed cyan) ---------------------------- */}
        {alternativeRoute.length > 1 ? (
          <g pointerEvents="none" data-testid="path-alternative">
            <polyline
              points={alternativeRoute.map((n) => `${n.x},${n.y}`).join(" ")}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: "var(--surface, #FFFFFF)", strokeWidth: 6, opacity: 0.7 }}
            />
            <polyline
              points={alternativeRoute.map((n) => `${n.x},${n.y}`).join(" ")}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: PALETTE.cyan, strokeWidth: 2.4, strokeDasharray: "9 6" }}
            />
            {alternativeRoute.slice(0, -1).map((node, index) => {
              const next = alternativeRoute[index + 1];
              const mid = pointAlong(
                { x1: node.x, y1: node.y, x2: next.x, y2: next.y } as LaidOutLink,
                0.72,
              );
              const angle = Math.atan2(next.y - node.y, next.x - node.x);
              return arrow(mid.x, mid.y, angle, 6, PALETTE.cyan, `alt-${node.id}-${next.id}`);
            })}
          </g>
        ) : null}

        {/* --- nodes ------------------------------------------------------ */}
        <g>
          {layout.nodes.map((node) => {
            const isSwitch = node.kind === "switch";
            const width = node.width;
            const height = node.height;
            const interactive = typeof onSelectNode === "function";
            const title = `${node.label} (${node.id}, ${node.kind})${node.ip ? `, ${node.ip}` : ""}${
              node.role ? `, ${node.role}` : ""
            }`;
            return (
              <g
                key={node.id}
                data-testid={`node-${node.id}`}
                data-kind={node.kind}
                transform={`translate(${node.x} ${node.y})`}
                tabIndex={interactive ? 0 : undefined}
                role={interactive ? "button" : undefined}
                aria-label={interactive ? title : undefined}
                onClick={interactive ? () => onSelectNode?.(node.id) : undefined}
                onKeyDown={
                  interactive
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelectNode?.(node.id);
                        }
                      }
                    : undefined
                }
                style={{ cursor: interactive ? "pointer" : undefined, outline: "none" }}
              >
                <title>{title}</title>
                <rect
                  x={-width / 2}
                  y={-height / 2}
                  width={width}
                  height={height}
                  rx={isSwitch ? 5 : 9}
                  style={{
                    fill: isSwitch ? "var(--surface, #FFFFFF)" : "var(--panel, #FFFFFF)",
                    stroke: isSwitch ? PALETTE.blue : PALETTE.muted,
                    strokeWidth: isSwitch ? 1.6 : 1.1,
                  }}
                />
                {isSwitch ? (
                  <>
                    {/* Switch glyph: a filled square inside the outlined square. */}
                    <rect
                      x={-4}
                      y={-height / 2 + 5}
                      width={8}
                      height={5}
                      rx={1.5}
                      style={{ fill: PALETTE.blue, opacity: 0.75 }}
                    />
                    <text
                      y={height / 2 - 6}
                      textAnchor="middle"
                      style={{ font: `600 10px ${FONT_SANS}`, fill: PALETTE.ink }}
                    >
                      {node.label}
                    </text>
                  </>
                ) : (
                  <>
                    <text
                      y={-1}
                      textAnchor="middle"
                      style={{ font: `600 10px ${FONT_SANS}`, fill: PALETTE.ink }}
                    >
                      {node.label}
                    </text>
                    <text y={9} textAnchor="middle" style={{ font: `8px ${FONT_MONO}`, fill: PALETTE.muted }}>
                      {node.ip ? node.ip.split("/")[0].split(".").slice(-1)[0] : "host"}
                    </text>
                  </>
                )}
              </g>
            );
          })}
        </g>

        {/* Endpoint markers so the direction of the demo flow is never ambiguous. */}
        {currentRoute.length > 1 ? (
          <g pointerEvents="none">
            <circle
              cx={currentRoute[0].x}
              cy={currentRoute[0].y}
              r={Math.max(currentRoute[0].width, currentRoute[0].height) / 2 + 5}
              fill="none"
              style={{ stroke: PALETTE.blue, strokeWidth: 1.2, opacity: 0.6 }}
            />
            <circle
              cx={currentRoute[currentRoute.length - 1].x}
              cy={currentRoute[currentRoute.length - 1].y}
              r={Math.max(currentRoute[currentRoute.length - 1].width, currentRoute[currentRoute.length - 1].height) / 2 + 5}
              fill="none"
              style={{ stroke: PALETTE.blue, strokeWidth: 1.2, opacity: 0.6, strokeDasharray: "3 3" }}
            />
          </g>
        ) : null}
      </svg>

      {showLegend ? (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "12px",
            fontSize: "11px",
            color: PALETTE.muted,
            fontFamily: FONT_SANS,
          }}
        >
          <LegendItem color={PALETTE.blue} text="Current path" dashed={false} />
          <LegendItem color={PALETTE.cyan} text="Alternative path" dashed />
          <LegendItem color={PALETTE.danger} text="Congested / down" dashed />
          <LegendItem color={PALETTE.muted} text="Line width + label = link utilisation" dashed={false} />
        </div>
      ) : null}

      {showFlows && flows.length > 0 ? (
        <table
          data-testid="flow-table"
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "11px",
            fontFamily: FONT_SANS,
            color: PALETTE.ink,
          }}
        >
          <caption style={{ captionSide: "top", textAlign: "left", color: PALETTE.muted, fontSize: "11px", paddingBottom: "4px" }}>
            Measured flows (from the live snapshot)
          </caption>
          <thead>
            <tr style={{ color: PALETTE.muted, textAlign: "left" }}>
              <th style={{ fontWeight: 600, padding: "3px 6px 3px 0" }}>Flow</th>
              <th style={{ fontWeight: 600, padding: "3px 6px" }}>Path</th>
              <th style={{ fontWeight: 600, padding: "3px 6px", textAlign: "right" }}>Latency</th>
              <th style={{ fontWeight: 600, padding: "3px 0 3px 6px", textAlign: "right" }}>Loss</th>
            </tr>
          </thead>
          <tbody>
            {flows.map((flow) => (
              <tr key={flow.flow_id} style={{ borderTop: `1px solid ${PALETTE.border}` }}>
                <td style={{ padding: "4px 6px 4px 0", fontFamily: FONT_MONO }}>
                  {flow.flow_id} <span style={{ color: PALETTE.muted }}>({flow.kind ?? "other"})</span>
                </td>
                <td style={{ padding: "4px 6px", fontFamily: FONT_MONO, color: PALETTE.muted }}>
                  {(flow.path ?? []).length > 1 ? pathToEdgeIds(flow.path).join(" ") : "unknown"}
                </td>
                <td
                  style={{
                    padding: "4px 6px",
                    textAlign: "right",
                    fontFamily: FONT_MONO,
                    color: flow.latency_ms < 0 ? PALETTE.muted : PALETTE.ink,
                  }}
                >
                  {flow.latency_ms < 0 ? "unknown" : `${fmt(flow.latency_ms, 1)} ms`}
                </td>
                <td style={{ padding: "4px 0 4px 6px", textAlign: "right", fontFamily: FONT_MONO }}>
                  {typeof flow.packet_loss_pct === "number" ? `${fmt(flow.packet_loss_pct, 1)}%` : "unknown"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

function LegendItem({ color, text, dashed }: { color: string; text: string; dashed: boolean }): React.JSX.Element {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
      <svg width="22" height="8" aria-hidden="true">
        <line
          x1="1"
          y1="4"
          x2="21"
          y2="4"
          style={{ stroke: color, strokeWidth: 2.4, strokeDasharray: dashed ? "6 4" : undefined }}
        />
      </svg>
      {text}
    </span>
  );
}

export default TopologyView;
