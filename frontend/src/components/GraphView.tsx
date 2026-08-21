import { useEffect, useRef, useState } from "react";
import { getGraph } from "../api";
import type { Batch, GraphData } from "../types";

interface Props {
  batchId: string;
  batch: Batch;
}

const NODE_COLORS: Record<string, string> = {
  company: "#4f8cff",
  invoice: "#3fb950",
  product: "#d29922",
};

const EDGE_COLORS: Record<string, string> = {
  bought: "#3fb950",
  sold: "#4f8cff",
  contains: "#d29922",
  transaction: "#f0883e",
};

export function GraphView({ batchId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGraph(batchId)
      .then(({ graph }) => {
        if (!cancelled) setData(graph);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  useEffect(() => {
    if (!data || !containerRef.current) return;
    let network: import("vis-network").Network | null = null;

    import("vis-network/standalone").then(({ Network }) => {
      if (!containerRef.current) return;
      const nodes: import("vis-network").Node[] = data.nodes.map((n) => ({
        id: n.id,
        label: n.label,
        group: n.type,
        title: `${n.type}\n${JSON.stringify(n.properties, null, 2)}`,
      }));
      const edges: import("vis-network").Edge[] = data.edges.map((e) => ({
        from: e.source,
        to: e.target,
        label: e.relation,
        arrows: "to",
        color: { color: EDGE_COLORS[e.relation] ?? "#3d444d" },
        font: { color: "#8b949e", size: 9, strokeWidth: 0 },
      }));
      network = new Network(
        containerRef.current,
        { nodes, edges },
        {
          groups: {
            company: { color: NODE_COLORS.company, shape: "dot", size: 16 },
            invoice: { color: NODE_COLORS.invoice, shape: "diamond", size: 14 },
            product: { color: NODE_COLORS.product, shape: "dot", size: 10 },
          },
          nodes: {
            font: { color: "#c9d1d9", size: 12, face: "monospace" },
            borderWidth: 0,
          },
          edges: { smooth: { enabled: true, type: "continuous", roundness: 0.5 } },
          physics: {
            enabled: true,
            solver: "forceAtlas2Based",
            forceAtlas2Based: { gravitationalConstant: -30, springLength: 120 },
            stabilization: { iterations: 120 },
          },
          interaction: { hover: true, tooltipDelay: 120 },
        }
      );
    });

    return () => {
      network?.destroy();
    };
  }, [data]);

  if (error) return <p className="error-text">{error}</p>;
  if (!data) return <p className="muted">Building graph…</p>;

  const topCompanies = (data.nodes
    .filter((n) => n.type === "company")
    .map((n) => ({
      name: n.label,
      count: (n.properties.invoice_count as number) ?? 0,
      amount: (n.properties.total_amount as number) ?? 0,
    }))
    .sort((a, b) => b.amount - a.amount)
    .slice(0, 6));

  return (
    <div className="graph-wrap">
      <div className="graph-canvas" ref={containerRef} />
      <aside className="graph-side">
        <p className="muted small">
          {data.statistics.total_nodes} nodes · {data.statistics.total_edges} edges
        </p>
        <p className="muted small">
          companies {data.statistics.node_types.company ?? 0} · invoices{" "}
          {data.statistics.node_types.invoice ?? 0} · products{" "}
          {data.statistics.node_types.product ?? 0}
        </p>
        <h4>Top companies by volume</h4>
        <ol className="top-list">
          {topCompanies.map((c) => (
            <li key={c.name}>
              <span>{c.name}</span>
              <span className="mono muted">
                ¥{c.amount.toFixed(2)} · {c.count}
              </span>
            </li>
          ))}
        </ol>
        <p className="muted small" style={{ marginTop: 12 }}>
          edges: {Object.entries(data.statistics.edge_types).map(([k, v]) => `${k} ${v}`).join(" · ")}
        </p>
      </aside>
    </div>
  );
}
