import type { AuditFinding, Severity } from "../types";

const SEVERITIES: Severity[] = ["CRITICAL", "ERROR", "WARNING", "INFO"];
const SEV_COLORS: Record<Severity, string> = {
  CRITICAL: "#dc2626",
  ERROR: "#ea580c",
  WARNING: "#ca8a04",
  INFO: "#6d737c",
};

interface Props {
  findings: AuditFinding[];
}

/** Horizontal bars: findings per audit rule. */
function RuleBars({ rules }: { rules: [string, number][] }) {
  const W = 720;
  const rowH = 26;
  const labelW = 170;
  const rightW = 56;
  const topPad = 8;
  const H = topPad + rules.length * rowH + 8;
  const max = Math.max(...rules.map((r) => r[1]), 1);
  const barW = W - labelW - rightW - 24;
  return (
    <div className="audit-chart">
      <h4>Findings by rule</h4>
      <svg viewBox={`0 0 ${W} ${H}`} className="analysis-svg" role="img" aria-label="findings by rule">
        {rules.map(([rule, count], i) => {
          const y = topPad + i * rowH;
          const bw = Math.max((count / max) * barW, 2);
          return (
            <g key={rule}>
              <text x={labelW - 10} y={y + 14} textAnchor="end" fontSize={10} fill="#6d737c">
                {rule}
              </text>
              <rect x={labelW} y={y + 3} width={bw} height={rowH - 8} rx={3} fill="#4f46e5" />
              <text x={labelW + bw + 6} y={y + 14} fontSize={10} fill="#17181c">
                {count}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Vertical bars: severity distribution. */
function SeverityBars({ data }: { data: { sev: Severity; count: number }[] }) {
  const W = 720;
  const H = 180;
  const labelW = 40;
  const bottomPad = 30;
  const topPad = 20;
  const innerW = W - labelW * 2;
  const innerH = H - topPad - bottomPad;
  const max = Math.max(...data.map((d) => d.count), 1);
  const colW = innerW / data.length;
  return (
    <div className="audit-chart">
      <h4>Findings by severity</h4>
      <svg viewBox={`0 0 ${W} ${H}`} className="analysis-svg" role="img" aria-label="findings by severity">
        {data.map((d, i) => {
          const h = (d.count / max) * innerH;
          const x = labelW + i * colW + colW * 0.25;
          const y = topPad + innerH - h;
          return (
            <g key={d.sev}>
              {d.count > 0 && (
                <text x={x + colW * 0.25} y={y - 6} textAnchor="middle" fontSize={10} fill="#17181c">
                  {d.count}
                </text>
              )}
              <rect x={x} y={y} width={colW * 0.5} height={Math.max(h, d.count > 0 ? 3 : 0)} rx={3} fill={SEV_COLORS[d.sev]} />
              <text x={x + colW * 0.25} y={H - bottomPad + 16} textAnchor="middle" fontSize={10} fill="#6d737c">
                {d.sev}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function AuditCharts({ findings }: Props) {
  if (findings.length === 0) {
    return (
      <div className="audit-charts">
        <div className="analysis-empty">No audit findings to chart.</div>
      </div>
    );
  }
  const ruleCounts = new Map<string, number>();
  for (const f of findings) {
    ruleCounts.set(f.rule_id, (ruleCounts.get(f.rule_id) ?? 0) + 1);
  }
  const rules = Array.from(ruleCounts.entries()).sort((a, b) => b[1] - a[1]);
  const sevData = SEVERITIES.map((sev) => ({
    sev,
    count: findings.filter((f) => f.severity === sev).length,
  }));
  return (
    <div className="audit-charts">
      <RuleBars rules={rules} />
      <SeverityBars data={sevData} />
    </div>
  );
}
