export function ConfidenceBar({ field, value }: { field: string; value: number }) {
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
