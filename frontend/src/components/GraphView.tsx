import { useEffect, useMemo, useRef, useState } from "react";
import { getGraph } from "../api";
import type { Batch, GraphData, GraphNode } from "../types";

interface Props {
  batchId: string;
  batch: Batch;
  /** External focus request (e.g. from the detail drawer's graph fragment). */
  focusRequest: string | null;
  onFocusHandled: () => void;
  onNodeClick: (node: GraphNode) => void;
}

const NODE_COLORS: Record<string, string> = {
  company: "#2563eb",
  invoice: "#16a34a",
  product: "#ca8a04",
};

const EDGE_STYLES: Record<string, { color: string; dashes: boolean }> = {
  bought: { color: "#16a34a", dashes: false },
  sold: { color: "#2563eb", dashes: false },
  contains: { color: "#ca8a04", dashes: true },
  transaction: { color: "#ea580c", dashes: true },
};

function nodeAmount(n: GraphNode): number {
  if (n.type === "invoice") return (n.properties.amount as number) ?? 0;
  return (n.properties.total_amount as number) ?? 0;
}

function fmtMoney(v: number): string {
  return "¥" + v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function GraphView({ batchId, focusRequest, onFocusHandled, onNodeClick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<import("vis-network").Network | null>(null);
  // public DataSet handles so node/edge styling can be updated without
  // touching the network's internal `body`
  const nodesDSRef = useRef<import("vis-data").DataSet<import("vis-network").Node> | null>(null);
  const edgesDSRef = useRef<import("vis-data").DataSet<import("vis-network").Edge> | null>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // filters
  const [types, setTypes] = useState<Set<string>>(new Set(["invoice", "company", "product"]));
  const [company, setCompany] = useState("");
  const [minAmount, setMinAmount] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);

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

  const companies = useMemo(
    () =>
      data
        ? Array.from(
            new Set(
              data.nodes
                .filter((n) => n.type === "company")
                .map((n) => n.label)
                .filter((l) => l.trim())
            )
          ).sort((a, b) => a.localeCompare(b))
        : [],
    [data]
  );

  /** Nodes matching filters + their direct neighbors; edges within that set. */
  const visible = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], edges: [] as GraphData["edges"] };
    const min = parseFloat(minAmount) || 0;
    let base = data.nodes.filter(
      (n) => types.has(n.type) && nodeAmount(n) >= min
    );
    if (company) base = base.filter((n) => n.type === "company" && n.label === company);

    const ids = new Set<string>(base.map((n) => n.id));
    if (company) {
      const ego = base[0]?.id;
      if (ego) {
        for (const e of data.edges) {
          if (e.source === ego || e.target === ego) {
            ids.add(e.source);
            ids.add(e.target);
          }
        }
      }
    } else {
      for (const e of data.edges) {
        if (ids.has(e.source) || ids.has(e.target)) {
          ids.add(e.source);
          ids.add(e.target);
        }
      }
    }
    const nodes = data.nodes.filter((n) => ids.has(n.id));
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
    return { nodes, edges };
  }, [data, types, company, minAmount]);

  const hasFilters = company !== "" || minAmount !== "" || types.size < 3;

  // (re)build the network when the visible subset changes
  useEffect(() => {
    if (!containerRef.current || visible.nodes.length === 0) {
      networkRef.current?.destroy();
      networkRef.current = null;
      nodesDSRef.current = null;
      edgesDSRef.current = null;
      return;
    }
    let network: import("vis-network").Network | null = null;
    let cancelled = false;

    import("vis-network/standalone").then(({ Network, DataSet }) => {
      if (cancelled || !containerRef.current) return;
      const nodes: import("vis-network").Node[] = visible.nodes.map((n) => ({
        id: n.id,
        label: n.label,
        group: n.type,
        title: `${n.type}\n${JSON.stringify(n.properties, null, 2)}`,
      }));
      const edges: import("vis-network").Edge[] = visible.edges.map((e, i) => {
        const style = EDGE_STYLES[e.relation] ?? { color: "#d1d5db", dashes: false };
        return {
          id: `${e.source}->${e.target}#${i}`,
          from: e.source,
          to: e.target,
          label: e.relation,
          arrows: "to",
          dashes: style.dashes,
          color: { color: style.color, highlight: style.color },
          font: { color: "#6d737c", size: 9, strokeWidth: 0 },
        };
      });
      const nodesDS = new DataSet(nodes);
      const edgesDS = new DataSet(edges);
      nodesDSRef.current = nodesDS;
      edgesDSRef.current = edgesDS;
      network = new Network(
        containerRef.current,
        { nodes: nodesDS, edges: edgesDS },
        {
          groups: {
            company: { color: NODE_COLORS.company, shape: "dot", size: 16 },
            invoice: { color: NODE_COLORS.invoice, shape: "diamond", size: 14 },
            product: { color: NODE_COLORS.product, shape: "dot", size: 10 },
          },
          nodes: {
            font: { color: "#17181c", size: 12, face: "monospace" },
            borderWidth: 0,
          },
          edges: {
            smooth: { enabled: true, type: "continuous", roundness: 0.5 },
            selectionWidth: 1.5,
          },
          physics: {
            enabled: true,
            solver: "forceAtlas2Based",
            forceAtlas2Based: {
              gravitationalConstant: -40,
              springLength: 220,
              springConstant: 0.045,
            },
            stabilization: { iterations: 160 },
          },
          interaction: { hover: true, tooltipDelay: 120, selectable: false },
        }
      );
      networkRef.current = network;
      network.on("click", (params) => {
        if (params.nodes.length > 0) {
          const id = params.nodes[0];
          setFocusId(id);
          const node = data?.nodes.find((n) => n.id === id);
          if (node) onNodeClick(node);
        } else {
          setFocusId(null); // click blank → restore overview
        }
      });
    });

    return () => {
      cancelled = true;
      network?.destroy();
      networkRef.current = null;
      nodesDSRef.current = null;
      edgesDSRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, data]);

  // focus styling: highlight the focused node + direct neighbours, dim the rest
  useEffect(() => {
    const nodesDS = nodesDSRef.current;
    const edgesDS = edgesDSRef.current;
    if (!nodesDS || !edgesDS || visible.nodes.length === 0) return;
    const neighbors = new Set<string>();
    if (focusId) {
      for (const e of visible.edges) {
        if (e.source === focusId) neighbors.add(e.target);
        if (e.target === focusId) neighbors.add(e.source);
      }
    }
    const nodeUpdates = visible.nodes.map((n) => {
      const involved = !focusId || n.id === focusId || neighbors.has(n.id);
      return {
        id: n.id,
        opacity: involved ? 1 : 0.18,
        borderWidth: n.id === focusId ? 3 : 0,
        color: {
          border: n.id === focusId ? "#111827" : NODE_COLORS[n.type],
          highlight: NODE_COLORS[n.type],
        },
      };
    });
    const edgeUpdates = visible.edges.map((e, i) => {
      const involved =
        !focusId ||
        e.source === focusId ||
        e.target === focusId ||
        neighbors.has(e.source) ||
        neighbors.has(e.target);
      return { id: `${e.source}->${e.target}#${i}`, opacity: involved ? 1 : 0.15 };
    });
    nodesDS.update(nodeUpdates);
    edgesDS.update(edgeUpdates);
  }, [focusId, visible]);

  // external focus request (from the detail drawer)
  useEffect(() => {
    if (!focusRequest) return;
    const network = networkRef.current;
    if (network && visible.nodes.some((n) => n.id === focusRequest)) {
      network.selectNodes([focusRequest]);
      network.focus(focusRequest, {
        scale: 1.25,
        animation: { duration: 450, easingFunction: "easeInOutQuad" },
      });
      setFocusId(focusRequest);
    }
    onFocusHandled();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest]);

  if (error) return <p className="error-text">{error}</p>;
  if (!data) return <p className="muted">Building graph…</p>;

  const totalAmount = visible.nodes
    .filter((n) => n.type === "invoice")
    .reduce((acc, n) => acc + nodeAmount(n), 0);
  const focusNode = focusId ? visible.nodes.find((n) => n.id === focusId) : null;
  const focusDegree = focusId
    ? visible.edges.filter((e) => e.source === focusId || e.target === focusId).length
    : 0;

  function toggleType(t: string) {
    setTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  function resetFilters() {
    setTypes(new Set(["invoice", "company", "product"]));
    setCompany("");
    setMinAmount("");
    setFocusId(null);
  }

  const topCompanies = data.nodes
    .filter((n) => n.type === "company")
    .map((n) => ({
      name: n.label,
      count: (n.properties.invoice_count as number) ?? 0,
      amount: (n.properties.total_amount as number) ?? 0,
    }))
    .sort((a, b) => b.amount - a.amount)
    .slice(0, 6);

  return (
    <div className="graph-page">
      <div className="graph-filters">
        <div className="graph-filter-group">
          <span className="graph-filter-label">Node types</span>
          {(["invoice", "company", "product"] as const).map((t) => (
            <label key={t} className="graph-check">
              <input
                type="checkbox"
                checked={types.has(t)}
                onChange={() => toggleType(t)}
              />
              <span className={`n-dot ${t}`} />
              {t}
            </label>
          ))}
        </div>
        <label className="graph-filter">
          <span>Company</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            <option value="">All companies</option>
            {companies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="graph-filter">
          <span>Min amount (¥)</span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={minAmount}
            onChange={(e) => setMinAmount(e.target.value)}
            placeholder="0"
          />
        </label>
        <button className="btn small ghost" onClick={resetFilters} disabled={!hasFilters}>
          Reset
        </button>
      </div>

      <div className="graph-stats">
        <span>
          <strong>{visible.nodes.length}</strong> nodes · <strong>{visible.edges.length}</strong> edges · total{" "}
          <strong className="mono">{fmtMoney(totalAmount)}</strong>
        </span>
        <span className="graph-legend">
          <span className="legend-item"><span className="n-dot invoice" />invoice</span>
          <span className="legend-item"><span className="n-dot company" />company</span>
          <span className="legend-item"><span className="n-dot product" />product</span>
          <span className="legend-item"><span className="e-line solid" />bought / sold</span>
          <span className="legend-item"><span className="e-line dashed" />contains / transaction</span>
        </span>
      </div>

      <div className="graph-wrap">
        <div className="graph-canvas-wrap">
          {visible.nodes.length === 0 ? (
            <div className="graph-canvas graph-canvas-empty">
              <span className="muted">No nodes match the filters.</span>
            </div>
          ) : (
            <div className="graph-canvas" ref={containerRef} />
          )}
          {focusNode && (
            <div className="graph-node-card">
              <div className="graph-node-card-title">{focusNode.label}</div>
              <div className="muted small">type · {focusNode.type}</div>
              <div className="mono">amount · {fmtMoney(nodeAmount(focusNode))}</div>
              <div className="mono">connections · {focusDegree}</div>
              <button className="btn small ghost" onClick={() => setFocusId(null)}>
                Clear focus
              </button>
            </div>
          )}
        </div>
        <aside className="graph-side">
          <h4>Top companies by volume</h4>
          <ol className="top-list">
            {topCompanies.map((c) => (
              <li key={c.name}>
                <span>{c.name}</span>
                <span className="mono muted">
                  {fmtMoney(c.amount)} · {c.count}
                </span>
              </li>
            ))}
          </ol>
          <p className="muted small" style={{ marginTop: 12 }}>
            click a node to focus its neighbourhood; click empty canvas to reset
          </p>
        </aside>
      </div>
    </div>
  );
}
