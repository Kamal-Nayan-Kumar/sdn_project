"use client";

import React, { useEffect, useMemo, useState } from "react";
import type {
  FlowTelemetry,
  NetworkSnapshot,
  TopologyCanvasProps,
  TopologyLink,
  TopologyNode,
} from "@/types";
import type { LinkTelemetry } from "../../../lib/types";
import {
  congestedLinkIds,
  layoutTopology,
  linksArray,
  pathToEdgeIds,
  pointAlong,
  type LaidOutLink,
  type LaidOutNode,
  type TopologyLayout,
} from "../../../lib/derive";

/**
 * TopologyCanvas — the live network, drawn from real state.
 *
 * Structure (nodes, links, capacities, delays) comes from
 * `GET /v1/topology` through the `/backend/*` proxy; telemetry
 * (utilisation, congested links, flow paths) comes from the snapshot
 * the shell polls. Geometry is `layoutTopology()` from `lib/derive`,
 * which is deterministic: the same lab always draws the same picture.
 *
 * Nothing here is invented. A link with no measurement is drawn
 * unmeasured (thin, neutral), and `snapshot === null` renders an
 * honest empty state rather than a sample graph.
 */

const TOPOLOGY_PATH = "/api/lab/topology";
const TOPOLOGY_REFRESH_MS = 30000;

interface TopologyResponse {
  nodes: TopologyNode[];
  links: TopologyLink[];
  controller: string | null;
}

function normaliseTopology(payload: unknown): TopologyResponse | null {
  if (!payload || typeof payload !== "object") return null;
  const obj = payload as Record<string, unknown>;
  const nodes = Array.isArray(obj.nodes) ? (obj.nodes as TopologyNode[]) : [];
  const links = Array.isArray(obj.links) ? (obj.links as TopologyLink[]) : [];
  if (nodes.length === 0 && links.length === 0) return null;
  return {
    nodes,
    links,
    controller: typeof obj.controller === "string" ? obj.controller : null,
  };
}

/** `snapshot.flows` arrives as an array from FastAPI, as an object
 * keyed by flow id from the lab. Both are accepted. */
function flowsArray(snapshot: NetworkSnapshot | null): FlowTelemetry[] {
  const raw = snapshot?.flows;
  if (!raw) return [];
  const values = Array.isArray(raw) ? raw : Object.values(raw);
  return values.filter((flow): flow is FlowTelemetry => !!flow && typeof flow === "object");
}

interface DrawnPath {
  key: string;
  nodeIds: string[];
  edgeIds: string[];
  kind: "flow" | "selected" | "alternative";
  label: string;
}

/** Paint for one link, derived from its measurement and state. */
function linkPaint(
  link: LaidOutLink,
  measured: LinkTelemetry | undefined,
  congested: boolean,
  suspect: boolean,
): { stroke: string; width: number; dash: boolean; opacity: number } {
  const util = measured?.utilization_pct;
  if (link.status === "down" || congested) {
    return { stroke: "var(--red-state)", width: 4, dash: congested, opacity: 1 };
  }
  if (suspect) {
    return { stroke: "var(--amber-state)", width: 3, dash: true, opacity: 1 };
  }
  if (link.status === "degraded" || (typeof util === "number" && util >= 92)) {
    return { stroke: "var(--red-state)", width: 3, dash: false, opacity: 1 };
  }
  if (typeof util === "number" && util >= 80) {
    return { stroke: "var(--amber-state)", width: 2.5, dash: false, opacity: 1 };
  }
  if (link.kind === "access") {
    return { stroke: "var(--line-strong)", width: 1.25, dash: false, opacity: 0.9 };
  }
  // Fabric link: line width carries utilisation, so a busy link is
  // visibly heavier than an idle one. Unmeasured links stay thin.
  const width = typeof util === "number" ? 2 + (Math.min(100, Math.max(0, util)) / 100) * 3 : 2;
  return { stroke: "var(--line-strong)", width, dash: false, opacity: 1 };
}

function PathArrow({ x, y, angle, color }: { x: number; y: number; angle: number; color: string }) {
  return (
    <polygon
      points="0,-3.4 7,0 0,3.4"
      transform={`translate(${x} ${y}) rotate(${angle})`}
      style={{ fill: color }}
      aria-hidden="true"
    />
  );
}

export function TopologyCanvas({
  snapshot,
  flowPaths: flowPathsProp,
  alternativePath,
  selectedPath,
  suspectLinks,
  congestedLinks,
  selectedFlowId,
  onSelectNode,
  onSelectLink,
  height = 420,
  className,
}: TopologyCanvasProps) {
  const [topology, setTopology] = useState<TopologyResponse | null>(null);
  const [topologyDetail, setTopologyDetail] = useState<string>("waiting for the first poll");

  // Structure poll: the graph changes when the lab is reset, so it is
  // re-read on a slow interval in addition to the mount fetch.
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();

    const load = async () => {
      try {
        const response = await fetch(TOPOLOGY_PATH, {
          credentials: "include",
          signal: controller.signal,
          cache: "no-store",
          headers: { accept: "application/json" },
        });
        if (!response.ok) {
          throw new Error(`returned HTTP ${response.status}`);
        }
        const payload = await response.json();
        if (stopped) return;
        const next = normaliseTopology(payload);
        setTopology(next);
        setTopologyDetail(
          next ? `${next.nodes.length} nodes · ${next.links.length} links` : "empty topology",
        );
      } catch (error) {
        if (stopped || (error as Error)?.name === "AbortError") return;
        setTopology(null);
        setTopologyDetail("topology service unreachable");
      }
    };

    void load();
    const timer = window.setInterval(load, TOPOLOGY_REFRESH_MS);
    return () => {
      stopped = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  const congested = useMemo(() => {
    const set = new Set(congestedLinkIds(snapshot));
    for (const id of congestedLinks ?? []) set.add(id);
    return set;
  }, [snapshot, congestedLinks]);

  const suspect = useMemo(() => new Set(suspectLinks ?? []), [suspectLinks]);

  const layout = useMemo<TopologyLayout | null>(
    () =>
      topology
        ? layoutTopology(topology.nodes, topology.links, { congestedLinkIds: [...congested] })
        : null,
    [topology, congested],
  );

  const telemetry = useMemo(() => {
    const out: Record<string, LinkTelemetry> = {};
    for (const link of linksArray(snapshot)) out[link.link_id] = link;
    return out;
  }, [snapshot]);

  // Paths to draw. The run store may pass evaluated paths through the
  // `flowPaths` prop; until then the snapshot's own flow paths are the
  // measured truth.
  const paths = useMemo<DrawnPath[]>(() => {
    const out: DrawnPath[] = [];
    let counter = 0;
    if (flowPathsProp && Object.keys(flowPathsProp).length > 0) {
      for (const [flowId, nodeIds] of Object.entries(flowPathsProp)) {
        if (!Array.isArray(nodeIds) || nodeIds.length < 2) continue;
        out.push({
          key: `flow-${flowId}-${counter++}`,
          nodeIds,
          edgeIds: pathToEdgeIds(nodeIds),
          kind: "flow",
          label: flowId,
        });
      }
    } else {
      for (const flow of flowsArray(snapshot)) {
        if (!flow.path || flow.path.length < 2) continue;
        out.push({
          key: `flow-${flow.flow_id}-${counter++}`,
          nodeIds: flow.path,
          edgeIds: pathToEdgeIds(flow.path),
          kind: "flow",
          label: flow.flow_id,
        });
      }
    }
    if (selectedPath && selectedPath.length > 1) {
      out.push({
        key: `selected-${counter++}`,
        nodeIds: selectedPath,
        edgeIds: pathToEdgeIds(selectedPath),
        kind: "selected",
        label: "selected path",
      });
    }
    if (alternativePath && alternativePath.length > 1) {
      out.push({
        key: `alternative-${counter++}`,
        nodeIds: alternativePath,
        edgeIds: pathToEdgeIds(alternativePath),
        kind: "alternative",
        label: "alternative path",
      });
    }
    return out;
  }, [flowPathsProp, snapshot, selectedPath, alternativePath]);

  // The contract: no snapshot, no pretend graph.
  if (!snapshot) {
    return (
      <div
        className={`topology-canvas ${className ?? ""}`}
        style={{ height: `${height}px` }}
        aria-label="Network topology"
      >
        <div className="topology-empty">
          <strong>Waiting for live network telemetry.</strong>
          <span>
            The topology appears once the backend at /backend/snapshot answers with a measured
            snapshot. {topologyDetail === "topology service unreachable" ? "The topology service is also unreachable." : ""}
          </span>
        </div>
      </div>
    );
  }

  if (!layout || !topology) {
    return (
      <div
        className={`topology-canvas ${className ?? ""}`}
        style={{ height: `${height}px` }}
        aria-label="Network topology"
      >
        <div className="topology-empty">
          <strong>Waiting for network topology…</strong>
          <span>{topologyDetail}</span>
        </div>
      </div>
    );
  }

  const pathPoints = (nodeIds: string[]): { x: number; y: number }[] =>
    nodeIds
      .map((id) => layout.byId[id])
      .filter((node): node is LaidOutNode => !!node)
      .map((node) => ({ x: node.x, y: node.y }));

  return (
    <div
      className={`topology-canvas plot-grid ${className ?? ""}`}
      style={{ height: `${height}px` }}
      aria-label="Network topology"
      data-detail={topologyDetail}
    >
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        style={{ display: "block" }}
        role="img"
        aria-label={`Network topology: ${layout.nodes.length} nodes, ${layout.links.length} links`}
      >
        {/* Links — fabric links carry utilisation as line width */}
        {layout.links.map((link) => {
          const measured = telemetry[link.id];
          const isCongested = congested.has(link.id);
          const isSuspect = suspect.has(link.id);
          const paint = linkPaint(link, measured, isCongested, isSuspect);
          const isSelected = paths.some((p) => p.edgeIds.includes(link.id));
          return (
            <g key={link.id}>
              <line
                x1={link.x1}
                y1={link.y1}
                x2={link.x2}
                y2={link.y2}
                stroke={paint.stroke}
                strokeWidth={paint.width}
                strokeDasharray={paint.dash ? "6,5" : undefined}
                strokeLinecap="round"
                opacity={paint.opacity}
                onClick={() => onSelectLink?.(link.id)}
                style={{ cursor: onSelectLink ? "pointer" : undefined }}
              >
                <title>
                  {link.id} · {link.source} ↔ {link.target}
                  {measured
                    ? ` · ${measured.utilization_pct.toFixed(0)}% of ${formatMbpsShort(measured.capacity_mbps)}`
                    : " · unmeasured"}
                  {link.delayMs ? ` · ${link.delayMs} ms` : ""}
                  {isCongested ? " · congested" : ""}
                </title>
              </line>
              {/* Utilisation label on fabric links, offset above the line */}
              {link.kind === "fabric" && measured ? (
                <text
                  x={link.midX}
                  y={link.midY - 6}
                  textAnchor="middle"
                  fontSize={10}
                  className="mono"
                  style={{ fill: "var(--ink-3)" }}
                >
                  {measured.utilization_pct.toFixed(0)}%
                </text>
              ) : null}
              {isSelected ? (
                <circle cx={link.midX} cy={link.midY} r={2.5} style={{ fill: "var(--blue)" }} aria-hidden="true" />
              ) : null}
            </g>
          );
        })}

        {/* Flow paths — measured forwarding paths, drawn over the links */}
        {paths
          .filter((path) => pathPoints(path.nodeIds).length >= 2)
          .map((path) => {
            const points = pathPoints(path.nodeIds);
            const isSelected = path.kind === "selected";
          const isAlternative = path.kind === "alternative";
          const isHighlighted =
            isSelected || isAlternative || (selectedFlowId !== undefined && path.key === selectedFlowId);
          const stroke = isAlternative
            ? "var(--cyan)"
            : "var(--blue)";
          const arrows: React.ReactNode[] = [];
          for (let i = 0; i + 1 < points.length; i += 1) {
            const a = points[i];
            const b = points[i + 1];
            const mid = pointAlong(
              { x1: a.x, y1: a.y, x2: b.x, y2: b.y } as LaidOutLink,
              0.5,
            );
            const angle = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
            arrows.push(
              <PathArrow
                key={`${path.key}-${i}`}
                x={mid.x}
                y={mid.y}
                angle={angle}
                color={stroke}
              />,
            );
          }
          return (
            <g key={path.key} opacity={isHighlighted ? 1 : 0.35}>
              <polyline
                points={points.map((p) => `${p.x},${p.y}`).join(" ")}
                fill="none"
                stroke={stroke}
                strokeWidth={isHighlighted ? 2.25 : 1.25}
                strokeDasharray={isAlternative ? "7,5" : undefined}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {isHighlighted ? arrows : null}
            </g>
          );
        })}

        {/* Nodes — hosts as labelled boxes, switches as circles */}
        {layout.nodes.map((node) => {
          const isSwitch = node.kind === "switch";
          const isController = node.kind === "controller";
          const nodeCongested = congested.has(node.id);
          const subtitle = isSwitch
            ? node.role || "switch"
            : node.ip || node.role || "host";
          return (
            <g
              key={node.id}
              onClick={() => onSelectNode?.(node.id)}
              style={{ cursor: onSelectNode ? "pointer" : undefined }}
            >
              {isSwitch || isController ? (
                <>
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={node.width / 2}
                    style={{
                      fill: "var(--surface)",
                      stroke: nodeCongested
                        ? "var(--red-state)"
                        : "var(--line-strong)",
                      strokeWidth: nodeCongested ? 2.5 : 1.5,
                    }}
                  />
                  {nodeCongested ? (
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={node.width / 2 - 4}
                      style={{ fill: "none", stroke: "var(--red-state)", strokeWidth: 1 }}
                      strokeDasharray="3,3"
                      aria-hidden="true"
                    />
                  ) : null}
                  <text
                    x={node.x}
                    y={node.y + 4}
                    textAnchor="middle"
                    fontSize={12}
                    className="mono"
                    style={{ fill: "var(--ink)", fontWeight: 500 }}
                  >
                    {node.label}
                  </text>
                </>
              ) : (
                <>
                  <rect
                    x={node.x - node.width / 2}
                    y={node.y - node.height / 2}
                    width={node.width}
                    height={node.height}
                    rx={3}
                    style={{
                      fill: "var(--surface)",
                      stroke: "var(--line-strong)",
                      strokeWidth: 1.5,
                    }}
                  />
                  <text
                    x={node.x}
                    y={node.y + 1}
                    textAnchor="middle"
                    fontSize={11}
                    className="mono"
                    style={{ fill: "var(--ink)" }}
                  >
                    {node.label}
                  </text>
                </>
              )}
              <text
                x={node.x}
                y={node.y + node.height / 2 + 14}
                textAnchor="middle"
                fontSize={9.5}
                className="mono"
                style={{ fill: "var(--ink-4)" }}
              >
                {subtitle}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Legend + live facts, top-right over the plot grid */}
      <div className="topology-overlay">
        <span className="legend-item">
          <span className="legend-swatch legend-swatch--normal" />
          normal
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-swatch--congested" />
          congested
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-swatch--alt legend-swatch--dashed" />
          alternative
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-swatch--selected" />
          flow path
        </span>
        <span className="legend-item legend-note">line width = utilisation</span>
      </div>

      <div className="topology-facts mono">{topologyDetail}</div>
    </div>
  );
}

function formatMbpsShort(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "? Mb/s";
  return value >= 1000 ? `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)} Gb/s` : `${value} Mb/s`;
}
