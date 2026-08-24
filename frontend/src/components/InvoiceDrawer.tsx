import { useI18n } from "../i18n";
import type { AuditFinding, BatchResult, GraphData, GraphNode } from "../types";
import { ConfidenceBar } from "./ConfidenceBar";

export type DrawerPayload =
  | { kind: "invoice"; result: BatchResult; index: number }
  | { kind: "node"; node: GraphNode };

interface Props {
  payload: DrawerPayload | null;
  findings: AuditFinding[];
  graph: GraphData | null;
  onClose: () => void;
  onFocusGraphNode: (nodeId: string) => void;
}

function fmtMoney(v: number): string {
  return "¥" + v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function displayValue(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return fmtMoney(v);
  return String(v);
}

/** Neighbors of a graph node: deduped id → { node, relations }. */
function neighborList(
  graph: GraphData | null,
  nodeId: string | null
): { id: string; node: GraphNode | undefined; relations: string[] }[] {
  if (!graph || !nodeId) return [];
  const map = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (e.source === nodeId) {
      const list = map.get(e.target) ?? [];
      list.push(e.relation);
      map.set(e.target, list);
    } else if (e.target === nodeId) {
      const list = map.get(e.source) ?? [];
      list.push(e.relation);
      map.set(e.source, list);
    }
  }
  return Array.from(map.entries()).map(([id, relations]) => ({
    id,
    node: graph.nodes.find((n) => n.id === id),
    relations,
  }));
}

function GraphFragment({
  nodeId,
  graph,
  onFocusGraphNode,
}: {
  nodeId: string | null;
  graph: GraphData | null;
  onFocusGraphNode: (nodeId: string) => void;
}) {
  const { t } = useI18n();
  const neighbors = neighborList(graph, nodeId);
  if (neighbors.length === 0) {
    return <p className="muted small">{t("drawer.noConnections")}</p>;
  }
  return (
    <ul className="neighbor-list">
      {neighbors.map(({ id, node, relations }) => (
        <li key={id}>
          <button className="neighbor-item" onClick={() => onFocusGraphNode(id)}>
            <span className={`n-dot ${node?.type ?? "unknown"}`} />
            <span>{node?.label ?? id}</span>
            <span className="mono muted small">{relations.join(" · ")}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function DrawerHeader({ title, subtitle, onClose }: { title: string; subtitle?: string; onClose: () => void }) {
  const { t } = useI18n();
  return (
    <div className="drawer-header">
      <div className="drawer-title">
        <strong>{title}</strong>
        {subtitle && <span className="muted small">{subtitle}</span>}
      </div>
      <button className="drawer-close" onClick={onClose} aria-label={t("drawer.close")}>
        ✕
      </button>
    </div>
  );
}

function FindingsSection({ findings }: { findings: AuditFinding[] }) {
  const { t } = useI18n();
  if (findings.length === 0) {
    return <p className="muted small">{t("drawer.noFindings")}</p>;
  }
  return (
    <ul className="drawer-findings">
      {findings.map((f, i) => (
        <li key={i} className={`finding ${f.severity.toLowerCase()}`}>
          <div className="finding-head">
            <span className={`badge ${f.severity.toLowerCase()}`}>
              {t(`severity.${f.severity.toLowerCase()}`)}
            </span>
            <span className="finding-rule mono">{f.rule_id}</span>
          </div>
          <p className="finding-message">{f.message}</p>
        </li>
      ))}
    </ul>
  );
}

function InvoiceView({
  result,
  index,
  findings,
  graph,
  onClose,
  onFocusGraphNode,
}: {
  result: BatchResult;
  index: number;
  findings: AuditFinding[];
  graph: GraphData | null;
  onClose: () => void;
  onFocusGraphNode: (nodeId: string) => void;
}) {
  const { t, tf } = useI18n();
  const doc = result.doc;
  if (!doc) {
    return (
      <div className="drawer-body">
        <DrawerHeader title={result.filename} subtitle={t("drawer.failed")} onClose={onClose} />
        <p className="error-text">{tf("results.failed", { error: result.error ?? "" })}</p>
      </div>
    );
  }

  const keyFields: [string, string | number | null][] = [
    ["invoice_number", doc.invoice_number],
    ["issue_date", doc.issue_date],
    ["invoice_type", doc.invoice_type],
    ["buyer", `${doc.buyer.name}${doc.buyer.tax_id ? " · " + doc.buyer.tax_id : ""}`],
    ["seller", `${doc.seller.name}${doc.seller.tax_id ? " · " + doc.seller.tax_id : ""}`],
    ["amount_excluding_tax", doc.amount_excluding_tax],
    ["tax_amount", doc.tax_amount],
    ["amount_including_tax", doc.amount_including_tax],
    ["issuer", doc.issuer],
    ["check_code", doc.check_code],
  ];
  const corrections = Object.entries(doc.corrections ?? {});
  const docFindings = findings.filter(
    (f) =>
      (f.invoice_number && f.invoice_number === doc.invoice_number) ||
      f.invoice_index === index
  );
  const invoiceNodeId = graph?.nodes.find(
    (n) => n.type === "invoice" && n.properties.number === doc.invoice_number
  )?.id ?? null;

  return (
    <div className="drawer-body">
      <DrawerHeader title={result.filename} subtitle={doc.invoice_number ?? undefined} onClose={onClose} />

      <section className="drawer-section">
        <h4>{t("drawer.fields")}</h4>
        <table className="kv">
          <tbody>
            {keyFields.map(([k, v]) => (
              <tr key={k}>
                <td className="muted">{k}</td>
                <td className="mono">{v ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {corrections.length > 0 && (
          <div className="correction-note">
            {corrections.map(([field, note]) => (
              <div key={field}>
                <strong>{field}</strong>: {note}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="drawer-section">
        <h4>{t("drawer.confidence")}</h4>
        {Object.entries(doc.confidence).length === 0 ? (
          <p className="muted small">{t("drawer.noConfidence")}</p>
        ) : (
          Object.entries(doc.confidence)
            .slice(0, 12)
            .map(([k, v]) => <ConfidenceBar key={k} field={k} value={v} />)
        )}
      </section>

      <section className="drawer-section">
        <h4>{t("drawer.items")}</h4>
        {doc.items.length === 0 ? (
          <p className="muted small">—</p>
        ) : (
          doc.items.map((it, i) => (
            <div key={i} className="item-card">
              <span className="item-name">{it.name}</span>
              <span className="mono muted small">
                {it.quantity ?? "—"} × {it.unit_price?.toFixed(2) ?? "—"} · {it.tax_rate ?? "—"}%
              </span>
              <span className="mono">
                {it.amount_excluding_tax?.toFixed(2) ?? "—"} + {it.tax_amount?.toFixed(2) ?? "—"}
              </span>
            </div>
          ))
        )}
      </section>

      <section className="drawer-section">
        <h4>{tf("drawer.evidence", { n: docFindings.length })}</h4>
        <FindingsSection findings={docFindings} />
      </section>

      <section className="drawer-section">
        <h4>{t("drawer.connections")}</h4>
        <GraphFragment nodeId={invoiceNodeId} graph={graph} onFocusGraphNode={onFocusGraphNode} />
      </section>
    </div>
  );
}

function NodeView({
  node,
  graph,
  onClose,
  onFocusGraphNode,
}: {
  node: GraphNode;
  graph: GraphData | null;
  onClose: () => void;
  onFocusGraphNode: (nodeId: string) => void;
}) {
  const { t } = useI18n();
  const props: [string, unknown][] = Object.entries(node.properties);
  return (
    <div className="drawer-body">
      <DrawerHeader title={node.label} subtitle={t(`graph.type.${node.type}`)} onClose={onClose} />
      <section className="drawer-section">
        <h4>{t("drawer.nodeProps")}</h4>
        <table className="kv">
          <tbody>
            {props.map(([k, v]) => (
              <tr key={k}>
                <td className="muted">{k}</td>
                <td className="mono">{displayValue(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="drawer-section">
        <h4>{t("drawer.connections")}</h4>
        <GraphFragment nodeId={node.id} graph={graph} onFocusGraphNode={onFocusGraphNode} />
      </section>
    </div>
  );
}

export function InvoiceDrawer({ payload, findings, graph, onClose, onFocusGraphNode }: Props) {
  return (
    <>
      {payload && <div className="drawer-overlay" onClick={onClose} />}
      <aside className={`drawer ${payload ? "open" : ""}`} aria-hidden={!payload}>
        {payload &&
          (payload.kind === "invoice" ? (
            <InvoiceView
              result={payload.result}
              index={payload.index}
              findings={findings}
              graph={graph}
              onClose={onClose}
              onFocusGraphNode={onFocusGraphNode}
            />
          ) : (
            <NodeView
              node={payload.node}
              graph={graph}
              onClose={onClose}
              onFocusGraphNode={onFocusGraphNode}
            />
          ))}
      </aside>
    </>
  );
}
