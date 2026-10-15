import React from "react";
import type {
  ActivityTimelineProps,
  ChatPanelProps,
  NetworkSnapshot,
  TopologyCanvasProps,
} from "@/types";

/**
 * Extension points for the parallel agent (agent B).
 *
 * Each slot is a labelled placeholder for a component that agent B owns under
 * `apps/web/src/components/**`. The shell never imports a module that does not
 * exist yet, so `npm run build` passes whether or not those files have landed.
 *
 * Mounting a slot is a one-line change in `src/app/page.tsx`:
 *
 *   import { TopologyCanvas } from "@/components/topology/TopologyCanvas";
 *   // replace
 *   <TopologySlot {...topologyProps} />
 *   // with
 *   <TopologyCanvas {...topologyProps} />
 *
 * The prop shapes are declared in `apps/web/types.ts` under
 * "Component contracts for the parallel agent" and repeated in `apps/web/README.md`.
 */

interface SlotFrameProps {
  title: string;
  subtitle?: string;
  /** Module agent B must create, shown verbatim in the placeholder. */
  modulePath: string;
  /** Export to import from that module. */
  exportName: string;
  /** The props the shell passes, listed for the other agent. */
  propsSignature: string;
  hint?: string;
  /** Plot-grid background — used for the canvas slots. */
  grid?: boolean;
  /** Live facts about the data this slot will receive. */
  facts?: string;
  /** Trailing content of the panel header (legend, live badge, ...). */
  headerExtra?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  style?: React.CSSProperties;
}

export function SlotFrame({
  title,
  subtitle,
  modulePath,
  exportName,
  propsSignature,
  hint,
  grid = false,
  facts,
  headerExtra,
  className,
  bodyClassName,
  style,
}: SlotFrameProps) {
  return (
    <section className={`panel ${className ?? ""}`} style={style} aria-label={title}>
      <header className="panel-head">
        <h2 className="panel-title">{title}</h2>
        {subtitle ? <span className="panel-sub">{subtitle}</span> : null}
        {facts ? <span className="panel-sub mono">{facts}</span> : null}
        <span className="flex-1" />
        {headerExtra}
      </header>
      <div className={`slot-placeholder ${grid ? "plot-grid" : ""} ${bodyClassName ?? ""}`}>
        <p className="slot-placeholder-mark">
          <span className="dot dot--idle" aria-hidden="true" />
          {title} awaiting component
        </p>
        <p className="slot-placeholder-note">
          Owned by the parallel agent. Create{" "}
          <code className="slot-placeholder-path">{modulePath}</code>, export{" "}
          <code className="slot-placeholder-path">{exportName}</code> with the props below, then mount
          it in <code className="slot-placeholder-path">src/app/page.tsx</code>.
        </p>
        <pre className="slot-placeholder-path" style={{ whiteSpace: "pre-wrap", textAlign: "left" }}>
          {propsSignature}
        </pre>
        {hint ? <p className="slot-placeholder-note">{hint}</p> : null}
      </div>
    </section>
  );
}

function describeSnapshot(snapshot: NetworkSnapshot | null): string {
  if (!snapshot) return "no snapshot";
  const topology = snapshot.topology ?? { nodes: [], links: [] };
  const { nodes, links } = topology;
  return `${nodes.length} nodes · ${links.length} links · ${snapshot.flows?.length ?? 0} flows`;
}

/** The four line states the topology canvas must be able to draw. */
export function TopologyLegend() {
  return (
    <div className="legend" aria-hidden="true">
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
        alternative path
      </span>
      <span className="legend-item">
        <span className="legend-swatch legend-swatch--selected" />
        selected path
      </span>
    </div>
  );
}

/* --------------------------------------------------------------------------
 * Network topology
 * -------------------------------------------------------------------------- */

export function TopologySlot(props: TopologyCanvasProps) {
  return (
    <SlotFrame
      title="Network topology"
      subtitle="live"
      grid
      className="min-h-0 flex-1"
      facts={describeSnapshot(props.snapshot)}
      modulePath="src/components/topology/TopologyCanvas.tsx"
      exportName="TopologyCanvas"
      headerExtra={<TopologyLegend />}
      propsSignature={`interface TopologyCanvasProps {
  snapshot: NetworkSnapshot | null;
  flowPaths?: Record<string, string[]>;
  alternativePath?: string[] | null;
  selectedPath?: string[] | null;
  suspectLinks?: string[];
  congestedLinks?: string[];
  selectedFlowId?: string | null;
  onSelectNode?: (nodeId: string | null) => void;
  onSelectLink?: (linkId: string | null) => void;
  height?: number;        // default 420
  className?: string;
}`}
      hint="SVG, drawn from real snapshot state: link utilisation, congested or failed links, the current path per flow and any candidate path. With snapshot === null show an honest empty state, never a sample graph."
    />
  );
}

/* --------------------------------------------------------------------------
 * Chat sidebar
 * -------------------------------------------------------------------------- */

export function ChatSlot(props: ChatPanelProps) {
  return (
    <SlotFrame
      title="Assistant"
      subtitle="conversation"
      className="min-h-0 flex-1"
      facts={props.conversationId ? `conversation ${props.conversationId}` : "no conversation"}
      modulePath="src/components/chat/ChatPanel.tsx"
      exportName="ChatPanel"
      propsSignature={`interface ChatPanelProps {
  conversationId: string | null;
  messages: ChatMessage[];
  agentStatus: AgentRunStatus;
  isStreaming: boolean;
  onSend: (text: string) => void | Promise<void>;
  onStop?: () => void;
  disabled?: boolean;
  className?: string;
}`}
      hint="Conversation history, user and assistant messages, streaming text, the message input and the stop/cancel control. No fabricated replies: with no live run the panel shows history and an idle agent."
    />
  );
}

/* --------------------------------------------------------------------------
 * Agent activity timeline
 * -------------------------------------------------------------------------- */

export function ActivitySlot(props: ActivityTimelineProps) {
  return (
    <SlotFrame
      title="Agent activity"
      subtitle="streamed"
      className="min-h-0 flex-1"
      facts={`${props.groups.length} groups`}
      modulePath="src/components/agent-activity/ActivityTimeline.tsx"
      exportName="ActivityTimeline"
      propsSignature={`interface ActivityTimelineProps {
  groups: ActivityGroup[];
  expandedKeys?: string[];
  onToggleGroup?: (key: string) => void;
  isStreaming?: boolean;
  className?: string;
}`}
      hint="Expandable groups keyed by ActivityStepKey: intent_detection, evidence_collection, diagnosis_summary, recovery_planning, tool_calls, safety_validation, execution, recovery_verification. Concise reasoning summaries and observable work only — never raw chain-of-thought, never invented tool calls."
    />
  );
}
