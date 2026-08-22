import type { BatchResult } from "../types";

interface Props {
  results: (BatchResult | null)[];
  onRetry?: (indices: number[]) => void;
  onRowClick?: (index: number) => void;
}

export function ResultsTable({ results, onRetry, onRowClick }: Props) {
  const rows = results.filter((r): r is BatchResult => r !== null);

  if (rows.length === 0) {
    return <p className="muted">No results yet.</p>;
  }

  return (
    <table className="results">
      <thead>
        <tr>
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
          <tr
            key={r.filename}
            className={`${r.success ? "" : "row-failed"} clickable`}
            onClick={() => onRowClick?.(i)}
          >
            <td className="mono">{r.filename}</td>
            <td className="mono">{r.doc?.invoice_number ?? "—"}</td>
            <td className="mono">{r.doc?.issue_date ?? "—"}</td>
            <td>{r.doc?.seller.name || "—"}</td>
            <td>{r.doc?.buyer.name || "—"}</td>
            <td className="num mono">
              {r.doc?.amount_including_tax != null
                ? `¥${r.doc.amount_including_tax.toFixed(2)}`
                : "—"}
              {!r.success && onRetry && (
                <button
                  className="retry-btn"
                  style={{ marginLeft: 8 }}
                  onClick={(e) => {
                    e.stopPropagation();
                    onRetry([i]);
                  }}
                >
                  Retry
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
