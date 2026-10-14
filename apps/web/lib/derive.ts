/**
 * Pure derivation helpers.
 *
 * Everything in this file is deterministic and side-effect free: no clock, no
 * `Math.random`, no network, no React. That is what makes the topology
 * geometry, the path -> edge-id conversion and the SSE event grouping unit
 * testable with plain Node (see `apps/web/lib/derive.ts` consumers).
 *
 * File is intentionally dependency-free (types only) so it can be imported
 * from a `.mjs` assertion script through Node's type stripping.
 */

import type {
  ActivityGroup,
  ActivityGroupKey,
  ActivityGroupStatus,
  AgentRunEvent,
  LinkStatus,
  LinkTelemetry,
  NetworkSnapshot,
  NodeKind,
  PathCandidate,
  PathsResponse,
  ToolCallRecord,
  TopologyLink,
  TopologyNode,
} from "./types";
import { ACTIVITY_GROUP_KEYS, ACTIVITY_GROUP_LABELS, TERMINAL_EVENT_TYPES } from "./types";

/* -------------------------------------------------------------------------- */
/* Links                                                                      */
/* -------------------------------------------------------------------------- */

/** Anything that carries a `links` payload we can read. */
export interface HasLinks {
  readonly links?: unknown;
}

const num = (value: unknown, fallback: number): number => {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const str = (value: unknown, fallback: string): string =>
  typeof value === "string" && value.length > 0 ? value : fallback;

/**
 * Edge id for a pair of nodes: the two ids sorted and joined with `-`.
 *
 * This mirrors `network-lab/topologies/multipath.py:switch_to_switch_link_id`
 * and `services/api/app/safety/gates.py:_links_along`, so the frontend and the
 * backend always agree on `s1-s2` regardless of traversal direction.
 */
export function edgeId(a: string, b: string): string {
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

/**
 * Convert a *node path* (`[h1, s1, s2, s4, h4]`) into the *edge ids* it
 * traverses (`["h1-s1", "s1-s2", "s2-s4", "s4-h4"]`).
 *
 * The lab reports flow paths as node sequences and links as edge ids; every
 * consumer that wants to highlight a path on the topology needs this.
 */
export function pathToEdgeIds(path: readonly string[] | null | undefined): string[] {
  if (!path) return [];
  const out: string[] = [];
  for (let i = 0; i + 1 < path.length; i += 1) {
    const a = path[i];
    const b = path[i + 1];
    if (typeof a !== "string" || typeof b !== "string" || a.length === 0 || b.length === 0) {
      continue;
    }
    if (a === b) continue; // a repeated node cannot form an edge
    const id = edgeId(a, b);
    if (out.length === 0 || out[out.length - 1] !== id) out.push(id);
  }
  return out;
}

/** The reverse of {@link edgeId}: is `linkId` the edge between `a` and `b`? */
export function isEdgeBetween(linkId: string, a: string, b: string): boolean {
  return linkId === edgeId(a, b);
}

function normaliseLink(raw: unknown, fallbackId?: string): LinkTelemetry | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const id = str(obj.link_id, fallbackId ?? "");
  if (!id) return null;
  const status = str(obj.status, "up");
  return {
    link_id: id,
    utilization_pct: num(obj.utilization_pct, 0),
    capacity_mbps: num(obj.capacity_mbps, 0),
    tx_bps: num(obj.tx_bps, 0),
    rx_bps: num(obj.rx_bps, 0),
    latency_ms: obj.latency_ms === null || obj.latency_ms === undefined ? null : num(obj.latency_ms, 0),
    status: status === "down" || status === "degraded" ? status : "up",
    measured_at: typeof obj.measured_at === "string" ? obj.measured_at : undefined,
  };
}

/**
 * Normalise a snapshot's `links` into an array.
 *
 * The lab serves `links` as an object keyed by `link_id`
 * (`network-lab/run_lab.py:snapshot`), while FastAPI serves a list. Both are
 * accepted; junk entries are dropped rather than guessed at, and the result is
 * sorted by `link_id` so rendering is stable.
 */
export function linksArray(snapshot: HasLinks | NetworkSnapshot | null | undefined): LinkTelemetry[] {
  const raw: unknown = snapshot ? (snapshot as HasLinks).links : null;
  if (raw === null || raw === undefined) return [];
  let out: LinkTelemetry[] = [];
  if (Array.isArray(raw)) {
    for (const entry of raw) {
      const link = normaliseLink(entry);
      if (link) out.push(link);
    }
  } else if (typeof raw === "object") {
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      const link = normaliseLink(value, key);
      if (link) out.push(link);
    }
  }
  out.sort((a, b) => (a.link_id < b.link_id ? -1 : a.link_id > b.link_id ? 1 : 0));
  return out;
}

/** `{link_id -> telemetry}` built from any snapshot shape. */
export function linkIndex(
  snapshot: HasLinks | NetworkSnapshot | null | undefined,
): Record<string, LinkTelemetry> {
  const out: Record<string, LinkTelemetry> = {};
  for (const link of linksArray(snapshot)) out[link.link_id] = link;
  return out;
}

/** Ids of links that are congested *or* not up. Never invents a congested link. */
export function congestedLinkIds(snapshot: NetworkSnapshot | null | undefined): string[] {
  const set = new Set<string>();
  for (const id of snapshot?.congested_links ?? []) {
    if (typeof id === "string" && id) set.add(id);
  }
  for (const link of linksArray(snapshot)) {
    if (link.status === "down" || link.status === "degraded") set.add(link.link_id);
  }
  return [...set].sort();
}

/* -------------------------------------------------------------------------- */
/* Flow paths -> path candidates                                              */
/* -------------------------------------------------------------------------- */

/**
 * Normalise `/v1/paths` into `PathCandidate[]`.
 *
 * The lab returns `{paths: [{nodes, switches, links, total_delay_ms, ...}]}`;
 * the backend's evaluate_paths tool returns `{candidates: [...]}`. Both are
 * accepted so the dashboard does not care which side produced the payload.
 */
export function pathCandidates(payload: PathsResponse | null | undefined): PathCandidate[] {
  if (!payload) return [];
  const raw: unknown[] = Array.isArray(payload.candidates)
    ? [...payload.candidates]
    : Array.isArray(payload.paths)
      ? [...payload.paths]
      : [];
  return raw.map((entry) => {
    const obj = (entry ?? {}) as Record<string, unknown>;
    const nodes = Array.isArray(obj.nodes) ? (obj.nodes as unknown[]).filter((n): n is string => typeof n === "string") : [];
    const links = Array.isArray(obj.links) ? (obj.links as unknown[]).filter((n): n is string => typeof n === "string") : [];
    return {
      nodes,
      links,
      total_delay_ms: num(obj.total_delay_ms, 0),
      min_capacity_mbps: typeof obj.min_capacity_mbps === "number" ? obj.min_capacity_mbps : null,
      max_utilization_pct: num(obj.max_utilization_pct, 0),
      avoids_congestion: typeof obj.avoids_congestion === "boolean" ? obj.avoids_congestion : undefined,
      avoids_protected: typeof obj.avoids_protected === "boolean" ? obj.avoids_protected : undefined,
    };
  });
}

/* -------------------------------------------------------------------------- */
/* Deterministic topology layout                                              */
/* -------------------------------------------------------------------------- */

export interface LaidOutNode {
  readonly id: string;
  readonly kind: NodeKind;
  readonly label: string;
  /** Centre point. */
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly column: number;
  readonly row: number;
  readonly role: string;
  readonly ip: string | null;
  readonly congested: boolean;
}

export interface LaidOutLink {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly status: LinkStatus;
  readonly congestible: boolean;
  readonly capacityMbps: number;
  readonly delayMs: number;
  readonly kind: "access" | "fabric";
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
  /** Point on the segment at `t` (0..1), handy for arrows and labels. */
  readonly midX: number;
  readonly midY: number;
}

export interface TopologyLayout {
  readonly width: number;
  readonly height: number;
  readonly nodes: readonly LaidOutNode[];
  readonly links: readonly LaidOutLink[];
  readonly byId: Readonly<Record<string, LaidOutNode>>;
  readonly columns: number;
  readonly rows: number;
  /** Link ids that could not be placed because an endpoint node is unknown. */
  readonly skippedLinkIds: readonly string[];
}

export interface LayoutOptions {
  readonly columnGap?: number;
  readonly rowGap?: number;
  readonly margin?: number;
  readonly hostWidth?: number;
  readonly hostHeight?: number;
  readonly switchSize?: number;
  readonly congestedLinkIds?: readonly string[] | null;
  /** Honour `TopologyNode.x` / `.y` when both are finite numbers. */
  readonly useHints?: boolean;
}

const DEFAULT_LAYOUT = {
  columnGap: 148,
  rowGap: 96,
  margin: 64,
  hostWidth: 54,
  hostHeight: 32,
  switchSize: 38,
} as const;

function inferKind(node: TopologyNode): NodeKind {
  if (node.kind === "host" || node.kind === "switch" || node.kind === "controller") return node.kind;
  if (/^s\d+$/.test(node.id)) return "switch";
  if (/^h\d+$/.test(node.id)) return "host";
  return "switch";
}

function roleOf(node: TopologyNode): string {
  const extra = node.extra;
  if (!extra || typeof extra !== "object") return "";
  const role = (extra as Record<string, unknown>).role;
  return typeof role === "string" ? role : "";
}

/**
 * Deterministic layout: switches are layered left-to-right by breadth-first
 * distance from the lowest-id switch, hosts move to a rail on the outside of
 * the switch they attach to. No randomness, no clock, no reliance on input
 * ordering beyond "sorted by id", so the same topology always lays out
 * identically.
 */
export function layoutTopology(
  nodes: readonly TopologyNode[] | null | undefined,
  links: readonly TopologyLink[] | null | undefined,
  options: LayoutOptions = {},
): TopologyLayout {
  const opts = { ...DEFAULT_LAYOUT, ...options };
  const congested = new Set(options.congestedLinkIds ?? []);

  // 1. Normalise nodes deterministically (sorted by id, de-duplicated).
  const byIdSource = new Map<string, TopologyNode>();
  for (const raw of nodes ?? []) {
    if (!raw || typeof raw.id !== "string" || raw.id.length === 0) continue;
    if (!byIdSource.has(raw.id)) byIdSource.set(raw.id, raw);
  }
  const ordered = [...byIdSource.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  const kinds = new Map<string, NodeKind>();
  for (const node of ordered) kinds.set(node.id, inferKind(node));

  // 2. Undirected adjacency over switch nodes (hosts never carry transit).
  const switchNeighbours = new Map<string, Set<string>>();
  for (const node of ordered) if (kinds.get(node.id) === "switch") switchNeighbours.set(node.id, new Set());
  const accessSwitchOf = new Map<string, string>();
  for (const link of links ?? []) {
    if (!link || typeof link.source !== "string" || typeof link.target !== "string") continue;
    const a = link.source;
    const b = link.target;
    if (!byIdSource.has(a) || !byIdSource.has(b)) continue;
    const aIsSwitch = kinds.get(a) === "switch";
    const bIsSwitch = kinds.get(b) === "switch";
    if (aIsSwitch && bIsSwitch) {
      switchNeighbours.get(a)?.add(b);
      switchNeighbours.get(b)?.add(a);
    } else if (aIsSwitch !== bIsSwitch) {
      const host = aIsSwitch ? b : a;
      const sw = aIsSwitch ? a : b;
      if (!accessSwitchOf.has(host)) accessSwitchOf.set(host, sw);
    }
  }

  // 3. Switch layers: BFS from the lowest switch id; unvisited islands get
  //    appended after the reachable layers so nothing overlaps.
  const layer = new Map<string, number>();
  const switchIds = [...switchNeighbours.keys()].sort();
  let nextLayer = 0;
  for (const root of switchIds) {
    if (layer.has(root)) continue;
    let frontier = [root];
    layer.set(root, nextLayer);
    let depth = nextLayer;
    while (frontier.length > 0) {
      depth += 1;
      const nextFrontier: string[] = [];
      for (const node of frontier) {
        for (const peer of switchNeighbours.get(node) ?? []) {
          if (layer.has(peer)) continue;
          layer.set(peer, depth);
          nextFrontier.push(peer);
        }
      }
      nextFrontier.sort();
      frontier = nextFrontier;
    }
    nextLayer = depth + 1;
  }

  const switchLayers = [...layer.values()];
  const minSwitchLayer = switchLayers.length > 0 ? Math.min(...switchLayers) : 0;
  const maxSwitchLayer = switchLayers.length > 0 ? Math.max(...switchLayers) : 0;

  // 4. Column per node.
  const column = new Map<string, number>();
  for (const node of ordered) {
    const id = node.id;
    if (kinds.get(id) === "switch") {
      column.set(id, layer.get(id) ?? 0);
      continue;
    }
    const attached = accessSwitchOf.get(id);
    if (!attached) {
      // Unattached host (should not happen in this lab): keep it on the right
      // rail so it never collides with the switch fabric.
      column.set(id, maxSwitchLayer + 1);
      continue;
    }
    const attachedLayer = layer.get(attached) ?? 0;
    if (minSwitchLayer === maxSwitchLayer) column.set(id, maxSwitchLayer + 1);
    else if (attachedLayer === minSwitchLayer) column.set(id, minSwitchLayer - 1);
    else if (attachedLayer === maxSwitchLayer) column.set(id, maxSwitchLayer + 1);
    else column.set(id, attachedLayer);
  }

  // 5. Rows: switches above hosts inside a column, both sorted by id.
  const columns = new Map<number, string[]>();
  for (const node of ordered) {
    const col = column.get(node.id) ?? 0;
    const bucket = columns.get(col);
    if (bucket) bucket.push(node.id);
    else columns.set(col, [node.id]);
  }
  const colIds = [...columns.keys()].sort((a, b) => a - b);
  const minCol = colIds.length > 0 ? colIds[0] : 0;
  const rank = (id: string): number => (kinds.get(id) === "switch" ? 0 : 1);
  let maxRows = 1;
  for (const col of colIds) {
    columns.get(col)!.sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0));
    maxRows = Math.max(maxRows, columns.get(col)!.length);
  }

  const laidOut: LaidOutNode[] = [];
  const byId: Record<string, LaidOutNode> = {};
  for (const col of colIds) {
    const ids = columns.get(col)!;
    const start = (maxRows - ids.length) / 2;
    for (let i = 0; i < ids.length; i += 1) {
      const id = ids[i];
      const src = byIdSource.get(id)!;
      const kind = kinds.get(id)!;
      const width = kind === "switch" ? opts.switchSize : opts.hostWidth;
      const height = kind === "switch" ? opts.switchSize : opts.hostHeight;
      const node: LaidOutNode = {
        id,
        kind,
        label: str(src.label, id),
        x: opts.margin + (col - minCol) * opts.columnGap,
        y: opts.margin + (start + i) * opts.rowGap,
        width,
        height,
        column: col,
        row: i,
        role: roleOf(src),
        ip: typeof src.ip === "string" ? src.ip : null,
        congested: congested.has(id),
      };
      laidOut.push(node);
      byId[id] = node;
    }
  }
  laidOut.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  // 6. Links between known nodes, shortened to the node borders so paths read
  //    clearly even when several links share a pair of nodes.
  const outLinks: LaidOutLink[] = [];
  const skippedLinkIds: string[] = [];
  for (const link of links ?? []) {
    const a = byId[link.source];
    const b = byId[link.target];
    if (!a || !b) {
      if (typeof link.id === "string" && link.id) skippedLinkIds.push(link.id);
      continue;
    }
    const status: LinkStatus = link.status === "down" || link.status === "degraded" ? link.status : "up";
    outLinks.push({
      id: link.id,
      source: link.source,
      target: link.target,
      status,
      congestible: link.congestible === true,
      capacityMbps: num(link.capacity_mbps, 0),
      delayMs: num(link.delay_ms, 0),
      kind: a.kind === "host" || b.kind === "host" ? "access" : "fabric",
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      midX: (a.x + b.x) / 2,
      midY: (a.y + b.y) / 2,
    });
  }
  outLinks.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  const width = opts.margin * 2 + Math.max(1, colIds.length - 1) * opts.columnGap;
  const height = opts.margin * 2 + (maxRows - 1) * opts.rowGap + opts.hostHeight;

  return { width, height, nodes: laidOut, links: outLinks, byId, columns: colIds.length, rows: maxRows, skippedLinkIds };
}

/**
 * Point at fraction `t` along a laid-out link, used for direction arrows and
 * for placing a label without it sitting exactly on the line.
 */
export function pointAlong(link: LaidOutLink, t: number): { x: number; y: number } {
  const clamped = t < 0 ? 0 : t > 1 ? 1 : t;
  return {
    x: link.x1 + (link.x2 - link.x1) * clamped,
    y: link.y1 + (link.y2 - link.y1) * clamped,
  };
}

/* -------------------------------------------------------------------------- */
/* Tool calls                                                                 */
/* -------------------------------------------------------------------------- */

/** Fold `tool_started` / `tool_completed` / `tool_failed` into ordered records. */
export function toolCallsFromEvents(events: readonly AgentRunEvent[] | null | undefined): ToolCallRecord[] {
  const out: ToolCallRecord[] = [];
  const openIndex = new Map<string, number>();
  for (const ev of events ?? []) {
    if (ev.type === "tool_started") {
      const record: ToolCallRecord = {
        tool: ev.tool,
        status: "started",
        inputs: ev.inputs ?? {},
        started_at: ev.ts,
        duration_ms: null,
        error: null,
        outputs: null,
        finished_at: null,
      };
      out.push(record);
      openIndex.set(ev.tool, out.length - 1);
      continue;
    }
    if (ev.type === "tool_completed" || ev.type === "tool_failed") {
      const failed = ev.type === "tool_failed";
      const status = failed ? "error" : (ev.status ?? "success");
      const idx = openIndex.get(ev.tool);
      const duration = ev.duration_ms ?? null;
      const error = failed ? ev.error : (ev.error ?? null);
      const outputs = failed ? null : (ev.outputs ?? null);
      if (idx !== undefined && idx >= 0 && idx < out.length) {
        const prev = out[idx];
        out[idx] = {
          ...prev,
          status,
          duration_ms: duration,
          error,
          outputs,
          finished_at: ev.ts,
        };
      } else {
        out.push({
          tool: ev.tool,
          status,
          inputs: {},
          outputs,
          duration_ms: duration,
          error,
          started_at: ev.ts,
          finished_at: ev.ts,
        });
      }
    }
  }
  return out;
}

/* -------------------------------------------------------------------------- */
/* Event grouping                                                             */
/* -------------------------------------------------------------------------- */

function isActivityGroupKey(key: string): key is ActivityGroupKey {
  return (ACTIVITY_GROUP_KEYS as readonly string[]).includes(key);
}

/**
 * Which activity group an event belongs to. `null` means "no group yet" (the
 * event still reaches every consumer that reads `events` directly).
 */
export function groupKeyForEvent(event: AgentRunEvent): ActivityGroupKey | "run" | "notes" | null {
  switch (event.type) {
    case "run_started":
    case "run_completed":
    case "run_failed":
    case "run_cancelled":
      return "run";
    case "note":
      return "notes";
    case "intent_detected":
    case "clarification_requested":
      return "intent_detection";
    case "evidence_assessed":
      return "evidence_collection";
    case "diagnosis_completed":
      return "diagnosis_summary";
    case "plan_proposed":
    case "path_evaluated":
      return "recovery_planning";
    case "tool_started":
    case "tool_completed":
    case "tool_failed":
      return "tool_calls";
    case "validation_completed":
    case "approval_requested":
    case "approval_decided":
      return "safety_validation";
    case "execution_started":
    case "execution_completed":
      return "execution";
    case "verification_completed":
      return "recovery_verification";
    case "step_started":
    case "step_completed": {
      const step = event.step;
      if (typeof step === "string" && isActivityGroupKey(step)) return step;
      return null;
    }
    case "message_delta":
      return null;
    default:
      return null;
  }
}

function latencyOf(flow: { latency_ms?: number } | null | undefined): number | null {
  if (!flow) return null;
  const value = flow.latency_ms;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function summarise(events: readonly AgentRunEvent[]): string {
  const notes: string[] = [];
  const add = (text: string): void => {
    if (text && !notes.includes(text)) notes.push(text);
  };
  for (const ev of events) {
    switch (ev.type) {
      case "step_started":
        add(ev.label ?? `Started ${ev.step}`);
        break;
      case "step_completed":
        add(ev.summary ?? `Finished ${ev.step}${ev.status === "failed" ? " (failed)" : ""}`);
        break;
      case "intent_detected": {
        const intent = ev.intent;
        if (ev.needs_clarification) {
          add("Waiting for clarification");
        } else if (intent) {
          add(`${intent.src_host} → ${intent.dst_host} under ${intent.max_latency_ms} ms`);
        } else {
          add("No structured intent parsed");
        }
        break;
      }
      case "clarification_requested":
        add(`Clarification: ${ev.question}`);
        break;
      case "tool_started":
        add(`Called ${ev.tool}`);
        break;
      case "tool_completed":
        add(
          `${ev.tool}: ${ev.status ?? "success"}${
            typeof ev.duration_ms === "number" ? ` in ${ev.duration_ms.toFixed(0)} ms` : ""
          }`,
        );
        break;
      case "tool_failed":
        add(`${ev.tool} failed: ${ev.error}`);
        break;
      case "evidence_assessed": {
        const a = ev.assessment;
        const confidence = typeof a.confidence === "number" ? `, confidence ${a.confidence.toFixed(2)}` : "";
        add(`Evidence ${a.status}${confidence}`);
        break;
      }
      case "diagnosis_completed":
        add(ev.summary || "Diagnosis recorded");
        break;
      case "plan_proposed": {
        const action = ev.action;
        add(action ? `Reroute ${action.flow_id} via ${action.path.join(" → ")}` : "No action proposed");
        break;
      }
      case "path_evaluated":
        add(`Evaluated ${ev.candidates.length} candidate path(s)`);
        break;
      case "validation_completed":
        add(
          ev.result.approved
            ? `Approved (${ev.result.checks?.length ?? 0} checks)`
            : `Rejected: ${ev.result.rejection_reason ?? "see checks"}`,
        );
        break;
      case "approval_requested":
        add(`Approval required (${ev.risk_level ?? "unknown"} risk)`);
        break;
      case "approval_decided":
        add(ev.approved ? `Approved by ${ev.decided_by ?? "operator"}` : "Approval declined");
        break;
      case "execution_started":
        add(`Executing${ev.action_id ? ` ${ev.action_id}` : ""}`);
        break;
      case "execution_completed":
        add(
          `Execution ${ev.result.status}${
            (ev.result.flow_rules_installed ?? 0) > 0
              ? `, ${ev.result.flow_rules_installed} flow rule(s) installed`
              : ""
          }`,
        );
        break;
      case "verification_completed": {
        const before = latencyOf(ev.outcome.before);
        const after = latencyOf(ev.outcome.after);
        add(
          before !== null && after !== null
            ? `Verification ${ev.outcome.status}: ${before.toFixed(1)} ms → ${after.toFixed(1)} ms`
            : `Verification ${ev.outcome.status}`,
        );
        break;
      }
      case "run_started":
        add(`Run started${ev.trigger ? ` (${ev.trigger})` : ""}`);
        break;
      case "run_completed":
        add(`Run completed: ${ev.outcome}`);
        break;
      case "run_failed":
        add(`Run failed: ${ev.error}`);
        break;
      case "run_cancelled":
        add("Run cancelled" + (ev.reason ? `: ${ev.reason}` : ""));
        break;
      default:
        break;
    }
  }
  if (notes.length === 0) return "No activity recorded yet";
  return notes.length <= 2 ? notes.join(" · ") : `${notes.slice(0, 2).join(" · ")} (+${notes.length - 2} more)`;
}

function groupStatusOf(key: ActivityGroupKey | "run" | "notes", events: readonly AgentRunEvent[]): ActivityGroupStatus {
  if (events.length === 0) return "pending";
  let sawStart = false;
  let sawFinish = false;
  let failed = false;
  let partial = false;
  for (const ev of events) {
    switch (ev.type) {
      case "step_started":
        sawStart = true;
        break;
      case "step_completed":
        sawFinish = true;
        if (ev.status === "failed") failed = true;
        break;
      case "tool_failed":
        failed = true;
        break;
      case "tool_completed":
        if ((ev.status ?? "success") === "error") failed = true;
        break;
      case "run_failed":
      case "run_cancelled":
        failed = true;
        break;
      case "run_completed":
        sawFinish = true;
        if (
          ev.outcome === "recovery_failed" ||
          ev.outcome === "cancelled" ||
          ev.outcome === "lab_offline" ||
          ev.outcome === "llm_unavailable" ||
          ev.outcome === "error"
        ) {
          failed = true;
        } else if (ev.outcome !== "intent_satisfied") {
          partial = true;
        }
        break;
      case "validation_completed":
        if (ev.result.approved === false) failed = true;
        break;
      case "evidence_assessed":
        if (ev.assessment.sufficient === false) partial = true;
        break;
      case "verification_completed":
        if (ev.outcome.status === "partial") partial = true;
        if (ev.outcome.status === "failed" || ev.outcome.status === "unverified") failed = true;
        break;
      case "execution_completed":
        if (ev.result.status !== "executed") failed = true;
        break;
      default:
        break;
    }
  }
  if (failed) return "failed";
  if (partial && !sawStart) return "partial";
  if (sawStart && !sawFinish && key !== "run") return "running";
  return "complete";
}

/**
 * Bucket a run's events into the eight spec-named activity groups (always
 * returned, in spec order, even when empty) plus any extra buckets
 * (`run`, `notes`) in first-seen order.
 *
 * Ordering and dedupe are the caller's job (see `agentRunReducer`); this
 * function only groups, so it is safe to call on every render.
 */
export function groupEvents(events: readonly AgentRunEvent[] | null | undefined): ActivityGroup[] {
  const buckets = new Map<string, AgentRunEvent[]>();
  for (const ev of events ?? []) {
    const key = groupKeyForEvent(ev);
    if (!key) continue;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(ev);
    else buckets.set(key, [ev]);
  }

  const build = (key: ActivityGroupKey | "run" | "notes", core: boolean): ActivityGroup => {
    const list = buckets.get(key) ?? [];
    const timestamps = list.map((e) => e.ts).filter((t): t is string => typeof t === "string" && t.length > 0);
    return {
      key,
      label: core ? ACTIVITY_GROUP_LABELS[key as ActivityGroupKey] : key === "run" ? "Run" : "Notes",
      core,
      status: groupStatusOf(key, list),
      events: list,
      summary: summarise(list),
      startedAt: timestamps.length > 0 ? timestamps[0] : null,
      finishedAt: timestamps.length > 0 ? timestamps[timestamps.length - 1] : null,
    };
  };

  const groups: ActivityGroup[] = ACTIVITY_GROUP_KEYS.map((key) => build(key, true));
  const extras: ("run" | "notes")[] = [];
  for (const key of buckets.keys()) if (key === "run" || key === "notes") extras.push(key);
  extras.sort();
  for (const key of extras) groups.push(build(key, false));
  return groups;
}

/** Convenience: only the groups that actually have events, in display order. */
export function activeGroups(events: readonly AgentRunEvent[] | null | undefined): ActivityGroup[] {
  return groupEvents(events).filter((g) => g.events.length > 0);
}

/** True once the run has reached a terminal event. */
export function isTerminalEvent(event: AgentRunEvent): boolean {
  return (TERMINAL_EVENT_TYPES as readonly string[]).includes(event.type);
}

/** Sum of switch link delays along a node path, using the topology's own data. */
export function pathDelayMs(path: readonly string[] | null | undefined, links: readonly TopologyLink[] | null | undefined): number {
  if (!path || !links) return 0;
  const byId = new Map<string, TopologyLink>();
  for (const link of links) if (link && typeof link.id === "string") byId.set(link.id, link);
  let total = 0;
  for (const id of pathToEdgeIds(path)) total += num(byId.get(id)?.delay_ms, 0);
  return total;
}
