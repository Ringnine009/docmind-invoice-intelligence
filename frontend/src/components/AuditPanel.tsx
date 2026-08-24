import { useI18n } from "../i18n";
import type { AuditFinding, Severity } from "../types";

interface Props {
  findings: AuditFinding[];
  summary: { total: number; by_severity: Record<Severity, number> } | null;
}

const SEVERITY_ORDER: Severity[] = ["CRITICAL", "ERROR", "WARNING", "INFO"];

export function AuditPanel({ findings, summary }: Props) {
  const { t } = useI18n();
  if (findings.length === 0) {
    return (
      <div className="audit-clean">
        <p className="ok-text">✓ {t("audit.clean")}</p>
      </div>
    );
  }

  const bySeverity = new Map<Severity, AuditFinding[]>();
  for (const f of findings) {
    const list = bySeverity.get(f.severity) ?? [];
    list.push(f);
    bySeverity.set(f.severity, list);
  }

  return (
    <div className="audit">
      <div className="severity-cards">
        {SEVERITY_ORDER.map((s) => {
          const count = summary?.by_severity[s] ?? bySeverity.get(s)?.length ?? 0;
          return (
            <div key={s} className={`sev-card ${s.toLowerCase()}`}>
              <span className="sev-count mono">{count}</span>
              <span className="sev-label">{t(`severity.${s.toLowerCase()}`)}</span>
            </div>
          );
        })}
      </div>

      <div className="findings">
        {SEVERITY_ORDER.map((s) => {
          const list = bySeverity.get(s);
          if (!list || list.length === 0) return null;
          return (
            <div key={s} className="findings-group">
              <h3 className={`group-title ${s.toLowerCase()}`}>
                {t(`severity.${s.toLowerCase()}`)}
              </h3>
              {list.map((f, i) => (
                <FindingCard key={`${s}-${i}`} f={f} />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FindingCard({ f }: { f: AuditFinding }) {
  const { t } = useI18n();
  const evidence = Object.entries(f.evidence).filter(
    ([, v]) => v !== null && v !== undefined && v !== ""
  );
  return (
    <div className={`finding ${f.severity.toLowerCase()}`}>
      <div className="finding-head">
        <span className={`badge ${f.severity.toLowerCase()}`}>
          {t(`severity.${f.severity.toLowerCase()}`)}
        </span>
        <span className="finding-rule mono">{f.rule_id}</span>
        <span className="muted small">
          {f.invoice_number
            ? `${t("results.invoiceNo")} ${f.invoice_number}`
            : f.invoice_index != null
              ? `#${f.invoice_index}`
              : ""}
        </span>
      </div>
      <p className="finding-message">{f.message}</p>
      {evidence.length > 0 && (
        <pre className="finding-evidence mono">
          {JSON.stringify(Object.fromEntries(evidence), null, 2)}
        </pre>
      )}
    </div>
  );
}
