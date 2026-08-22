import { useMemo, useState } from "react";
import type { AuditFinding, BatchResult } from "../types";
import { AuditCharts } from "./AuditCharts";

interface Props {
  results: (BatchResult | null)[];
  findings: AuditFinding[];
}

interface Row {
  invoice_number: string | null;
  issue_date: string | null;
  buyer: string;
  seller: string;
  amount: number | null;
}

interface SeriesPoint {
  label: string; // "2024-07-20" (daily) or "2024-07" (monthly)
  value: number;
}

// ---- data helpers ----------------------------------------------------------

function collectRows(results: (BatchResult | null)[]): Row[] {
  const rows: Row[] = [];
  for (const r of results) {
    const doc = r?.doc;
    if (!r?.success || !doc) continue;
    rows.push({
      invoice_number: doc.invoice_number,
      issue_date: doc.issue_date,
      buyer: doc.buyer?.name ?? "",
      seller: doc.seller?.name ?? "",
      amount: doc.amount_including_tax ?? null,
    });
  }
  return rows;
}

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.filter((v) => v.trim())))
    .sort((a, b) => a.localeCompare(b));
}

function matches(
  row: Row,
  dateFrom: string,
  dateTo: string,
  buyer: string,
  seller: string
): boolean {
  if (dateFrom && (!row.issue_date || row.issue_date < dateFrom)) return false;
  if (dateTo && (!row.issue_date || row.issue_date > dateTo)) return false;
  if (buyer && row.buyer !== buyer) return false;
  if (seller && row.seller !== seller) return false;
  return true;
}

/** Aggregate by day; fall back to month when more than 30 distinct days. */
function buildSeries(rows: Row[]): SeriesPoint[] {
  const dated = rows
    .filter((r) => r.issue_date && r.amount != null)
    .sort((a, b) => (a.issue_date! < b.issue_date! ? -1 : 1));

  const daily = new Map<string, number>();
  for (const r of dated) {
    daily.set(r.issue_date!, (daily.get(r.issue_date!) ?? 0) + (r.amount ?? 0));
  }

  const grouped = new Map<string, number>();
  const labelOf = (date: string) =>
    daily.size > 30 ? date.slice(0, 7) : date; // "YYYY-MM" vs "YYYY-MM-DD"
  for (const [date, sum] of daily) {
    const label = labelOf(date);
    grouped.set(label, (grouped.get(label) ?? 0) + sum);
  }

  return Array.from(grouped.entries())
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([label, value]) => ({ label, value: round2(value) }));
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function fmtMoney(v: number): string {
  return "¥" + v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// ---- chart geometry --------------------------------------------------------

const W = 720;
const H = 260;
const M = { top: 16, right: 18, bottom: 28, left: 78 };
const TICKS = 5;
const LINE = "#4f46e5";
const GRID = "#e3e5ea";
const TEXT = "#6d737c";

function Chart({ series }: { series: SeriesPoint[] }) {
  if (series.length === 0) {
    return (
      <div className="analysis-empty">No invoices match the filters.</div>
    );
  }

  const innerW = W - M.left - M.right;
  const innerH = H - M.top - M.bottom;
  const yMax = Math.max(...series.map((p) => p.value), 1) * 1.08;

  const x = (i: number) =>
    series.length === 1
      ? M.left + innerW / 2
      : M.left + (innerW * i) / (series.length - 1);
  const y = (v: number) => M.top + innerH * (1 - v / yMax);

  const linePath = series
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(" ");
  const areaPath =
    `${linePath} L${x(series.length - 1).toFixed(1)},${(M.top + innerH).toFixed(1)} ` +
    `L${x(0).toFixed(1)},${(M.top + innerH).toFixed(1)} Z`;

  // tick marks (0..yMax) + horizontal grid lines
  const ticks = Array.from({ length: TICKS + 1 }, (_, i) => {
    const v = (yMax * i) / TICKS;
    return { v, y: y(v) };
  });

  // x labels: at most 8, evenly spaced incl. first & last
  const maxLabels = Math.min(series.length, 8);
  const labelIdx = new Set<number>();
  for (let i = 0; i < maxLabels; i++) {
    labelIdx.add(Math.round((i * (series.length - 1)) / Math.max(maxLabels - 1, 1)));
  }

  const showDots = series.length <= 30;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="invoice amount trend"
      className="analysis-svg"
    >
      {ticks.map((t, i) => (
        <g key={i}>
          <line
            x1={M.left}
            x2={W - M.right}
            y1={t.y}
            y2={t.y}
            stroke={GRID}
            strokeWidth={1}
            strokeDasharray={i === 0 ? "0" : "3 3"}
          />
          <text
            x={M.left - 8}
            y={t.y + 3}
            textAnchor="end"
            fontSize={10}
            fill={TEXT}
          >
            {fmtMoney(t.v)}
          </text>
        </g>
      ))}

      <path d={areaPath} fill={LINE} fillOpacity={0.08} />
      <path
        d={linePath}
        fill="none"
        stroke={LINE}
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {showDots &&
        series.map((p, i) => (
          <circle key={i} cx={x(i)} cy={y(p.value)} r={2.5} fill={LINE} />
        ))}

      {Array.from(labelIdx).map((i) => (
        <text
          key={i}
          x={x(i)}
          y={H - M.bottom + 16}
          textAnchor="middle"
          fontSize={10}
          fill={TEXT}
        >
          {series[i].label}
        </text>
      ))}
    </svg>
  );
}

// ---- component -------------------------------------------------------------

export function AnalysisPanel({ results, findings }: Props) {
  const rows = useMemo(() => collectRows(results), [results]);
  const buyers = useMemo(() => uniqueSorted(rows.map((r) => r.buyer)), [rows]);
  const sellers = useMemo(() => uniqueSorted(rows.map((r) => r.seller)), [rows]);

  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [buyer, setBuyer] = useState("");
  const [seller, setSeller] = useState("");

  const filtered = useMemo(
    () =>
      rows.filter((r) =>
        matches(r, dateFrom, dateTo, buyer, seller)
      ),
    [rows, dateFrom, dateTo, buyer, seller]
  );
  const series = useMemo(() => buildSeries(filtered), [filtered]);

  const total = round2(
    filtered.reduce((acc, r) => acc + (r.amount ?? 0), 0)
  );
  const hasFilters = dateFrom !== "" || dateTo !== "" || buyer !== "" || seller !== "";

  function clearFilters() {
    setDateFrom("");
    setDateTo("");
    setBuyer("");
    setSeller("");
  }

  return (
    <div className="analysis">
      <div className="analysis-filters">
        <label className="analysis-filter">
          <span>Date from</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </label>
        <label className="analysis-filter">
          <span>Date to</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </label>
        <label className="analysis-filter">
          <span>Buyer</span>
          <select value={buyer} onChange={(e) => setBuyer(e.target.value)}>
            <option value="">All buyers</option>
            {buyers.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <label className="analysis-filter">
          <span>Seller</span>
          <select value={seller} onChange={(e) => setSeller(e.target.value)}>
            <option value="">All sellers</option>
            {sellers.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn small ghost"
          onClick={clearFilters}
          disabled={!hasFilters}
        >
          Clear filters
        </button>
      </div>

      <p className="analysis-stats">
        <strong>{filtered.length}</strong> records · total{" "}
        <strong className="mono">{fmtMoney(total)}</strong>
      </p>

      <div className="analysis-chart">
        <Chart series={series} />
      </div>

      <AuditCharts findings={findings} />

      <table className="results analysis-table">
        <thead>
          <tr>
            <th>Invoice №</th>
            <th>Issue date</th>
            <th>Seller</th>
            <th>Buyer</th>
            <th className="num">Total</th>
          </tr>
        </thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr>
              <td colSpan={5} className="muted">
                No invoices match the filters.
              </td>
            </tr>
          ) : (
            filtered.map((r, i) => (
              <tr key={i}>
                <td className="mono">{r.invoice_number ?? "—"}</td>
                <td className="mono">{r.issue_date ?? "—"}</td>
                <td>{r.seller || "—"}</td>
                <td>{r.buyer || "—"}</td>
                <td className="num mono">
                  {r.amount != null ? fmtMoney(r.amount) : "—"}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
