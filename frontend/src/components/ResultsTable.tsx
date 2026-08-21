import { useState } from "react";
import type { BatchResult, InvoiceDoc } from "../types";

interface Props {
  results: (BatchResult | null)[];
}

function ConfidenceBar({ field, value }: { field: string; value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.9 ? "ok" : value >= 0.6 ? "warn" : "bad";
  return (
    <div className="conf-row">
      <span className="conf-field mono">{field}</span>
      <div className="conf-track">
        <div className={`conf-fill ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="conf-value mono">{pct}%</span>
    </div>
  );
}

function InvoiceDetail({ doc }: { doc: InvoiceDoc }) {
  const keyFields: [string, string | number | null][] = [
    ["invoice_number", doc.invoice_number],
    ["issue_date", doc.issue_date],
    ["buyer", `${doc.buyer.name}${doc.buyer.tax_id ? " · " + doc.buyer.tax_id : ""}`],
    ["seller", `${doc.seller.name}${doc.seller.tax_id ? " · " + doc.seller.tax_id : ""}`],
    ["amount_excluding_tax", doc.amount_excluding_tax],
    ["tax_amount", doc.tax_amount],
    ["amount_including_tax", doc.amount_including_tax],
    ["issuer", doc.issuer],
    ["check_code", doc.check_code],
  ];
  return (
    <div className="detail">
      <div className="detail-cols">
        <div className="detail-left">
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
          <div className="detail-confidence">
            <p className="muted small" style={{ marginTop: 8 }}>model confidence</p>
            {Object.entries(doc.confidence).slice(0, 12).map(([k, v]) => (
              <ConfidenceBar key={k} field={k} value={v} />
            ))}
            {Object.keys(doc.confidence).length === 0 && (
              <p className="muted small">no confidence reported</p>
            )}
          </div>
        </div>
        <div className="detail-right">
          <p className="muted small">line items</p>
          {doc.items.length === 0 && <p className="muted small">—</p>}
          {doc.items.map((it, i) => (
            <div key={i} className="item-card">
              <span className="item-name">{it.name}</span>
              <span className="mono muted small">
                {it.quantity ?? "—"} × {it.unit_price?.toFixed(2) ?? "—"} · {it.tax_rate ?? "—"}%
              </span>
              <span className="mono">
                {it.amount_excluding_tax?.toFixed(2) ?? "—"} + {it.tax_amount?.toFixed(2) ?? "—"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ResultsTable({ results }: Props) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const rows = results.filter((r): r is BatchResult => r !== null);

  if (rows.length === 0) {
    return <p className="muted">No results yet.</p>;
  }

  function toggle(i: number) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  return (
    <table className="results">
      <thead>
        <tr>
          <th />
          <th>File</th>
          <th>Invoice №</th>
          <th>Date</th>
          <th>Seller</th>
          <th>Buyer</th>
          <th className="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <ResultRow key={r.filename} r={r} open={open.has(i)} onToggle={() => toggle(i)} />
        ))}
      </tbody>
    </table>
  );
}

function ResultRow({
  r,
  open,
  onToggle,
}: {
  r: BatchResult;
  open: boolean;
  onToggle: () => void;
}) {
  const doc = r.doc;
  return (
    <>
      <tr className={r.success ? "" : "row-failed"}>
        <td>
          <button className="row-toggle" onClick={onToggle} aria-label="expand">
            {open ? "▾" : "▸"}
          </button>
        </td>
        <td className="mono">{r.filename}</td>
        <td className="mono">{doc?.invoice_number ?? "—"}</td>
        <td className="mono">{doc?.issue_date ?? "—"}</td>
        <td>{doc?.seller.name || "—"}</td>
        <td>{doc?.buyer.name || "—"}</td>
        <td className="num mono">
          {doc?.amount_including_tax != null ? `¥${doc.amount_including_tax.toFixed(2)}` : "—"}
        </td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td colSpan={7}>
            {r.success && doc ? (
              <InvoiceDetail doc={doc} />
            ) : (
              <p className="error-text">extraction failed: {r.error}</p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
