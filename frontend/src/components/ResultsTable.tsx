import { useI18n } from "../i18n";
import type { BatchResult } from "../types";

interface Props {
  results: (BatchResult | null)[];
  onRetry?: (indices: number[]) => void;
  onRowClick?: (index: number) => void;
}

export function ResultsTable({ results, onRetry, onRowClick }: Props) {
  const { t } = useI18n();
  const rows = results.filter((r): r is BatchResult => r !== null);

  if (rows.length === 0) {
    return <p className="muted">{t("results.noResults")}</p>;
  }

  return (
    <table className="results">
      <thead>
        <tr>
          <th>{t("results.file")}</th>
          <th>{t("results.invoiceNo")}</th>
          <th>{t("results.date")}</th>
          <th>{t("results.seller")}</th>
          <th>{t("results.buyer")}</th>
          <th className="num">{t("results.amount")}</th>
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
                  {t("retry.single")}
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
