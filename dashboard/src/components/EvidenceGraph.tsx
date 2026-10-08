// Evidence graph — lightweight SVG force-directed layout.
// No external graph library needed; keeps bundle size within performance budgets.
import { memo, useCallback, useMemo, useRef, useState } from "react";
import type { EvidenceGraphResponse, GraphNode } from "../types";

// ── Layout constants ───────────────────────────────────────────────────────────
const REPULSION = 2800;
const SPRING_LEN = 90;
const SPRING_K = 0.04;
const DAMPING = 0.85;
const ITERATIONS = 90;

const NODE_R = 8;

const RISK_COLORS: Record<string, string> = {
  CRITICAL: "var(--color-red)",
  HIGH: "var(--color-amber)",
  MEDIUM: "#b8a040",
  LOW: "var(--color-teal)",
  asset: "var(--color-indigo)",
  evidence: "var(--color-muted)",
};
const RISK_FILTERS = ["all", "CRITICAL", "HIGH", "LOW", "MEDIUM"] as const;

function deterministicUnit(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

// ── Simple force simulation ────────────────────────────────────────────────────
function simulate(
  nodes: { x: number; y: number; vx: number; vy: number }[],
  edges: { s: number; t: number }[],
): void {
  for (let iter = 0; iter < ITERATIONS; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = nodes[j].x - nodes[i].x;
        let dy = nodes[j].y - nodes[i].y;
        let d2 = dx * dx + dy * dy || 1;
        let f = REPULSION / d2;
        let d = Math.sqrt(d2);
        let fx = (dx / d) * f;
        let fy = (dy / d) * f;
        nodes[i].vx -= fx;
        nodes[i].vy -= fy;
        nodes[j].vx += fx;
        nodes[j].vy += fy;
      }
    }
    for (const e of edges) {
      let a = nodes[e.s],
        b = nodes[e.t];
      let dx = b.x - a.x,
        dy = b.y - a.y;
      let d = Math.sqrt(dx * dx + dy * dy) || 1;
      let f = (d - SPRING_LEN) * SPRING_K;
      let fx = (dx / d) * f,
        fy = (dy / d) * f;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }
    const temp = DAMPING - iter * 0.004;
    const damp = temp > 0.3 ? temp : 0.3;
    for (const n of nodes) {
      n.vx *= damp;
      n.vy *= damp;
      n.x += n.vx;
      n.y += n.vy;
    }
  }
}

// ── Component ──────────────────────────────────────────────────────────────────
interface Props {
  data: EvidenceGraphResponse;
  maxNodes?: number;
}

export const EvidenceGraph = memo(function EvidenceGraph({ data, maxNodes = 200 }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<GraphNode, SVGGElement>());
  const [hover, setHover] = useState<{ node: GraphNode; x: number; y: number } | null>(null);
  const [selected, setSelected] = useState<{
    node: GraphNode;
    x: number;
    y: number;
  } | null>(null);
  const [rovingNode, setRovingNode] = useState<GraphNode | null>(null);
  const [filter, setFilter] = useState<string>("all");

  // Limit nodes for performance
  const nodes = useMemo(() => data.nodes.slice(0, maxNodes), [data.nodes, maxNodes]);
  const nodeIds = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);
  const nodeLabels = useMemo(
    () => new Map(data.nodes.map((node) => [node.id, node.label])),
    [data.nodes],
  );
  const edges = useMemo(
    () => data.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target)),
    [data.edges, nodeIds],
  );

  // Initial layout — circle for assets, scattered around
  const assetNodes = useMemo(() => nodes.filter((n) => n.type === "asset"), [nodes]);

  const layout = useMemo(() => {
    const cx = 400,
      cy = 300,
      r = 200;
    let assetIndex = 0;
    const pts = nodes.map((node, index) => {
      if (node.type === "asset") {
        const angle = (2 * Math.PI * assetIndex++) / Math.max(assetNodes.length, 1);
        return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle), vx: 0, vy: 0 };
      }
      const seed = `${data.scan_id}:${node.id}:${index}`;
      return {
        x: cx + (deterministicUnit(`${seed}:x`) - 0.5) * 400,
        y: cy + (deterministicUnit(`${seed}:y`) - 0.5) * 400,
        vx: 0,
        vy: 0,
      };
    });
    const idxMap = new Map<string, number>();
    nodes.forEach((n, i) => idxMap.set(n.id, i));
    const edgeIdx = edges
      .map((e) => ({ s: idxMap.get(e.source)!, t: idxMap.get(e.target)! }))
      .filter((e) => e.s != null && e.t != null);
    simulate(pts, edgeIdx);
    return pts;
  }, [nodes, edges, assetNodes.length, data.scan_id]);

  // Filtered display
  const visibleNodes = useMemo(() => {
    if (filter === "all") return nodes;
    return nodes.filter((n) => (n.type === "asset" ? n.priority_label === filter : true));
  }, [nodes, filter]);

  const visibleIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

  // Build lookup for layout positions
  const posMap = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>();
    nodes.forEach((n, i) => m.set(n.id, layout[i] || { x: 400, y: 300 }));
    return m;
  }, [nodes, layout]);

  const nodeColor = useCallback((n: GraphNode): string => {
    if (n.type === "evidence") return RISK_COLORS.evidence;
    if (n.quantum_vulnerable) return "#ef4444";
    return RISK_COLORS[n.priority_label || ""] || RISK_COLORS.asset;
  }, []);

  const isLarge = nodes.length > 50;
  const focusableNode =
    rovingNode && visibleNodes.includes(rovingNode) ? rovingNode : visibleNodes[0];
  const detailCandidate = hover ?? selected;
  const detail =
    detailCandidate && visibleIds.has(detailCandidate.node.id) ? detailCandidate : null;

  return (
    <div className="graph-container">
      {isLarge && (
        <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
          Showing {nodes.length} of {data.nodes.length} nodes. Use the filter to narrow results.
        </p>
      )}
      <div className="graph-toolbar">
        <label className="muted" style={{ fontSize: 12, marginRight: 6 }} htmlFor="graph-filter">
          Filter:
        </label>
        <select id="graph-filter" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {RISK_FILTERS.map((l) => (
            <option key={l} value={l}>
              {l === "all" ? "All risk levels" : l}
            </option>
          ))}
        </select>
        <span className="muted" style={{ fontSize: 12, marginLeft: 12 }}>
          {visibleNodes.length} node{visibleNodes.length !== 1 ? "s" : ""}, {data.edges.length}{" "}
          edges
        </span>
      </div>
      <div ref={wrapRef} className="graph-wrap">
        <svg
          ref={svgRef}
          viewBox="0 0 800 600"
          role="img"
          aria-label={`Evidence graph with ${visibleNodes.length} nodes`}
          className="graph-svg"
        >
          <title>Evidence dependency graph</title>
          <desc>
            Force-directed graph showing cryptographic assets (colored by risk level) connected to
            their evidence sources. Scroll to zoom, drag to pan.
          </desc>
          <defs>
            <filter id="glow">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {/* Edges */}
          <g className="graph-edges">
            {data.edges.map((e, i) => {
              const sp = posMap.get(e.source);
              const tp = posMap.get(e.target);
              if (!sp || !tp) return null;
              if (!visibleIds.has(e.source) || !visibleIds.has(e.target)) return null;
              return (
                <line
                  key={`${e.source}-${e.target}-${i}`}
                  x1={sp.x}
                  y1={sp.y}
                  x2={tp.x}
                  y2={tp.y}
                  stroke="var(--color-rule)"
                  strokeWidth={1}
                  opacity={0.5}
                />
              );
            })}
          </g>
          {/* Nodes */}
          <g className="graph-nodes">
            {visibleNodes.map((n, index) => {
              const p = posMap.get(n.id);
              if (!p) return null;
              const col = nodeColor(n);
              const isAsset = n.type === "asset";
              return (
                <g
                  key={`${n.id}-${index}`}
                  ref={(element) => {
                    if (element) nodeRefs.current.set(n, element);
                    else nodeRefs.current.delete(n);
                  }}
                  transform={`translate(${p.x},${p.y})`}
                  onMouseEnter={() => setHover({ node: n, x: p.x, y: p.y })}
                  onMouseMove={(ev) => {
                    const rect = (ev.target as SVGElement).closest("svg")!.getBoundingClientRect();
                    const svgEl = svgRef.current!;
                    const vb = svgEl.viewBox.baseVal;
                    const sx = vb.width / rect.width;
                    const sy = vb.height / rect.height;
                    const hx = (ev.clientX - rect.left) * sx + vb.x;
                    const hy = (ev.clientY - rect.top) * sy + vb.y;
                    setHover({ node: n, x: hx, y: hy });
                  }}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => {
                    setRovingNode(n);
                    setHover({ node: n, x: p.x, y: p.y });
                  }}
                  onBlur={() => setHover(null)}
                  onClick={() =>
                    setSelected((current) =>
                      current?.node === n ? null : { node: n, x: p.x, y: p.y },
                    )
                  }
                  onKeyDown={(event) => {
                    if (
                      ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(
                        event.key,
                      )
                    ) {
                      event.preventDefault();
                      const currentIndex = visibleNodes.indexOf(n);
                      const lastIndex = visibleNodes.length - 1;
                      let nextIndex = currentIndex;
                      if (event.key === "Home") nextIndex = 0;
                      else if (event.key === "End") nextIndex = lastIndex;
                      else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
                        nextIndex = (currentIndex + 1) % visibleNodes.length;
                      } else {
                        nextIndex = (currentIndex - 1 + visibleNodes.length) % visibleNodes.length;
                      }
                      const nextNode = visibleNodes[nextIndex];
                      setRovingNode(nextNode);
                      nodeRefs.current.get(nextNode)?.focus();
                      return;
                    }
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    setSelected((current) =>
                      current?.node === n ? null : { node: n, x: p.x, y: p.y },
                    );
                  }}
                  tabIndex={n === focusableNode ? 0 : -1}
                  role="button"
                  aria-pressed={selected?.node === n}
                  aria-describedby={detail?.node === n ? "graph-node-details" : undefined}
                  aria-label={`${n.type}: ${n.label}${isAsset && n.priority_label ? `, ${n.priority_label}` : ""}`}
                  className="graph-node"
                >
                  <title>{n.label}</title>
                  <circle
                    r={NODE_R}
                    fill={col}
                    stroke={isAsset ? col : "none"}
                    strokeWidth={isAsset ? 2.5 : 0}
                    opacity={0.9}
                    filter={isAsset ? "url(#glow)" : "none"}
                  />
                  {!isAsset && (
                    <circle
                      r={NODE_R - 3}
                      fill="none"
                      stroke={col}
                      strokeWidth={1.5}
                      opacity={0.7}
                    />
                  )}
                  {isAsset && n.quantum_vulnerable && (
                    <circle
                      r={NODE_R + 2}
                      fill="none"
                      stroke="#ef4444"
                      strokeWidth={1.5}
                      opacity={0.6}
                    />
                  )}
                </g>
              );
            })}
          </g>
        </svg>
        {/* Tooltip */}
        {detail && (
          <div
            id="graph-node-details"
            role="status"
            className="graph-tooltip"
            style={{
              left: `${Math.min(detail.x + 16, 760)}px`,
              top: `${Math.max(detail.y - 10, 10)}px`,
            }}
          >
            <strong>{detail.node.label}</strong>
            <span className={`badge badge-${detail.node.type}`}>{detail.node.type}</span>
            {detail.node.type === "asset" && (
              <>
                {detail.node.priority_label && (
                  <p>
                    Priority: {detail.node.priority_label} ({detail.node.priority_score ?? "—"}/100)
                  </p>
                )}
                {detail.node.quantum_vulnerable != null && (
                  <p>Quantum vulnerable: {detail.node.quantum_vulnerable ? "Yes" : "No"}</p>
                )}
                {detail.node.confidence != null && (
                  <p>Evidence confidence: {Math.round(detail.node.confidence * 100)}%</p>
                )}
                {detail.node.operation_anchor && (
                  <p className="muted">Op: {detail.node.operation_anchor}</p>
                )}
              </>
            )}
          </div>
        )}
      </div>
      <div className="table-wrap">
        <table aria-label="Evidence relationships">
          <caption>Evidence relationships</caption>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">Relationship</th>
              <th scope="col">Target</th>
            </tr>
          </thead>
          <tbody>
            {data.edges.map((edge, index) => (
              <tr key={`${edge.source}-${edge.target}-${edge.relation}-${index}`}>
                <td>{nodeLabels.get(edge.source) ?? edge.source}</td>
                <td>{edge.relation}</td>
                <td>{nodeLabels.get(edge.target) ?? edge.target}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Legend */}
      <div className="graph-legend">
        <span className="legend-item">
          <span className="legend-dot" style={{ background: RISK_COLORS.asset }} /> Asset node
        </span>
        <span className="legend-item">
          <span className="legend-dot outline" style={{ borderColor: RISK_COLORS.evidence }} />{" "}
          Evidence source
        </span>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: "#ef4444" }} /> Quantum-vulnerable
        </span>
        <span className="legend-item muted">Scroll to zoom, drag to pan</span>
      </div>
    </div>
  );
});

export default EvidenceGraph;

/** The asset record has direct source edges only; present those edges as readable relationships. */
export function FocusedEvidenceGraph({ data }: { data: EvidenceGraphResponse }) {
  const [sourceFilter, setSourceFilter] = useState("all");
  const asset = data.nodes.find((node) => node.type === "asset");
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  const relationships = asset
    ? data.edges.flatMap((edge) => {
        const sourceId =
          edge.target === asset.id ? edge.source : edge.source === asset.id ? edge.target : null;
        const source = sourceId ? byId.get(sourceId) : undefined;
        return source?.type === "evidence" ? [{ source, relation: edge.relation }] : [];
      })
    : [];
  const sourceLabels = [...new Set(relationships.map(({ source }) => source.label))];
  const visible = relationships.filter(
    ({ source }) => sourceFilter === "all" || source.label === sourceFilter,
  );

  if (!asset) return <p className="muted">No asset relationship was returned for this finding.</p>;

  return (
    <div className="focused-evidence">
      <div className="focused-evidence-toolbar">
        <label htmlFor="focused-evidence-filter">Evidence source</label>
        <select
          id="focused-evidence-filter"
          value={sourceFilter}
          onChange={(event) => setSourceFilter(event.target.value)}
        >
          <option value="all">All sources</option>
          {sourceLabels.map((label) => (
            <option key={label} value={label}>
              {label}
            </option>
          ))}
        </select>
        <span>
          {visible.length} direct relationship{visible.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="focused-evidence-asset">
        <span className="eyebrow">Asset finding</span>
        <strong>{asset.label}</strong>
      </div>
      {visible.length > 0 ? (
        <ul className="focused-evidence-list">
          {visible.map(({ source, relation }) => (
            <li key={source.id}>
              <span className="focused-evidence-relation">{relation.replace(/_/g, " ")}</span>
              <span className="focused-evidence-source">
                <span className="eyebrow">Evidence source</span>
                <strong>{source.label}</strong>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No direct evidence sources match this filter.</p>
      )}
      <p className="muted">
        Each row links this finding to one recorded supporting source. Other findings are shown in
        the scan inventory.
      </p>
    </div>
  );
}
