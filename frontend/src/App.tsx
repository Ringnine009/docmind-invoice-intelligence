import { useEffect, useState } from "react";
import { getBatch, loadDemo, retryFailed, uploadInvoices } from "./api";
import { useI18n } from "./i18n";
import type { Batch, GraphNode } from "./types";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { AuditPanel } from "./components/AuditPanel";
import { GraphView } from "./components/GraphView";
import { InvoiceDrawer, type DrawerPayload } from "./components/InvoiceDrawer";
import { ResultsTable } from "./components/ResultsTable";
import { UploadZone } from "./components/UploadZone";

type Tab = "results" | "audit" | "analysis" | "graph";

const TAB_ORDER: Tab[] = ["results", "audit", "analysis", "graph"];

export default function App() {
  const { lang, setLang, t, tf } = useI18n();
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [tab, setTab] = useState<Tab>("results");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [drawer, setDrawer] = useState<DrawerPayload | null>(null);
  const [graphFocus, setGraphFocus] = useState<string | null>(null);

  useEffect(() => {
    if (!batchId) return;
    let cancelled = false;
    let timer: number | undefined;
    const id = batchId;

    async function poll() {
      try {
        const b = await getBatch(id);
        if (cancelled) return;
        setBatch(b);
        if (b.status === "pending" || b.status === "running") {
          timer = window.setTimeout(poll, 1200);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }
    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [batchId, refreshKey]);

  async function handleDemo() {
    setBusy(true);
    setError(null);
    try {
      const res = await loadDemo(30);
      setBatchId(res.batch_id);
      setTab("results");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload(files: File[]) {
    setBusy(true);
    setError(null);
    try {
      const res = await uploadInvoices(files);
      setBatchId(res.batch_id);
      setTab("results");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRetry(indices: number[]) {
    if (!batchId || indices.length === 0) return;
    setError(null);
    try {
      await retryFailed(batchId, indices);
      setRefreshKey((k) => k + 1); // restart polling
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function handleRowClick(index: number) {
    const r = batch?.results[index];
    if (!r) return;
    setDrawer({ kind: "invoice", result: r, index });
  }

  function handleGraphNodeClick(node: GraphNode) {
    if (!batch) return;
    if (node.type === "invoice") {
      const number = node.properties.number as string | undefined;
      const idx = batch.results.findIndex(
        (r) => r?.success && r.doc && r.doc.invoice_number === number
      );
      const r = idx >= 0 ? batch.results[idx] : null;
      if (r && r.doc) {
        setDrawer({ kind: "invoice", result: r, index: idx });
        return;
      }
    }
    setDrawer({ kind: "node", node });
  }

  function handleFocusGraphNode(nodeId: string) {
    setGraphFocus(nodeId);
    setDrawer(null);
    setTab("graph");
  }

  const running = batch?.status === "pending" || batch?.status === "running";
  const progress = batch && batch.total > 0 ? Math.round((batch.done / batch.total) * 100) : 0;
  const succeeded = batch?.results.filter((r) => r?.success).length ?? 0;
  const failed = batch?.results.filter((r) => r && !r.success).length ?? 0;
  const findingsTotal = batch?.audit_summary?.total ?? 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <div>
            <h1>DocMind</h1>
            <p>{t("brand.subtitle")}</p>
          </div>
        </div>
        <div className="topbar-actions">
          <div className="lang-switch" role="group" aria-label="language">
            <button
              className={lang === "en" ? "active" : ""}
              onClick={() => setLang("en")}
            >
              {t("lang.en")}
            </button>
            <button
              className={lang === "zh" ? "active" : ""}
              onClick={() => setLang("zh")}
            >
              {t("lang.zh")}
            </button>
          </div>
          {batch && (
            <span className={`chip source-${batch.source}`} title={batch.source}>
              {batch.source === "demo" ? t("source.demo") : t("source.upload")}
            </span>
          )}
          {batchId && (
            <span className="chip mono" title={batchId}>
              {t("batch.label")} {batchId.slice(0, 8)}
            </span>
          )}
          <button className="btn ghost" onClick={handleDemo} disabled={busy}>
            {busy ? t("upload.processing") : t("demo.load")}
          </button>
        </div>
      </header>

      {error && (
        <div className="banner error">
          <span>⚠</span> {error}
        </div>
      )}

      <UploadZone onUpload={handleUpload} busy={busy} />

      {batch && (
        <section className="statusbar">
          <span className={`chip status ${batch.status}`}>
            {t(`status.${batch.status}`)}
          </span>
          {running ? (
            <div className="progress">
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${progress}%` }} />
              </div>
              <span className="mono">
                {tf("status.progress", { done: batch.done, total: batch.total, pct: progress })}
              </span>
            </div>
          ) : (
            <span className="mono">
              {tf("status.summary", {
                done: succeeded,
                total: batch.total,
                findings: findingsTotal,
              })}
              {failed > 0 ? ` · ${failed} ${lang === "zh" ? "失败" : "failed"}` : ""}
            </span>
          )}
        </section>
      )}

      {batch && (
        <>
          <nav className="tabs">
            {TAB_ORDER.map((tKey) => {
              const disabled = tKey === "analysis" && batch.status !== "done";
              const label =
                tKey === "results"
                  ? `${t("tab.results")} (${batch.total})`
                  : tKey === "audit"
                    ? `${t("tab.audit")} (${findingsTotal})`
                    : t(`tab.${tKey}`);
              return (
                <button
                  key={tKey}
                  className={`tab ${tab === tKey ? "active" : ""}`}
                  disabled={disabled}
                  title={disabled ? t("analysis.gated") : undefined}
                  onClick={() => setTab(tKey)}
                >
                  {label}
                </button>
              );
            })}
            <div className="tabs-spacer" />
            {batch.status === "done" && (
              <div className="export-group">
                {failed > 0 && (
                  <button
                    className="btn small ghost"
                    onClick={() => {
                      const indices = batch.results
                        .map((r, i) => (r && !r.success ? i : -1))
                        .filter((i) => i >= 0);
                      handleRetry(indices);
                    }}
                  >
                    {tf("retry.failed", { n: failed })}
                  </button>
                )}
                <a className="btn small" href={`/api/batches/${batch.id}/export?format=csv`}>
                  {t("export.csv")}
                </a>
                <a className="btn small ghost" href={`/api/batches/${batch.id}/export?format=json`}>
                  {t("export.json")}
                </a>
              </div>
            )}
          </nav>

          <main className="content">
            {tab === "results" && (
              <ResultsTable
                results={batch.results}
                onRetry={handleRetry}
                onRowClick={handleRowClick}
              />
            )}
            {tab === "audit" && (
              <AuditPanel findings={batch.findings} summary={batch.audit_summary} />
            )}
            {tab === "analysis" &&
              (batch.status === "done" ? (
                <AnalysisPanel results={batch.results} findings={batch.findings} />
              ) : (
                <p className="muted">{t("analysis.gated")}</p>
              ))}
            {tab === "graph" && (
              <GraphView
                batchId={batch.id}
                batch={batch}
                focusRequest={graphFocus}
                onFocusHandled={() => setGraphFocus(null)}
                onNodeClick={handleGraphNodeClick}
              />
            )}
          </main>
        </>
      )}

      {!batch && (
        <main className="content empty">
          <p>{t("empty.title")}</p>
          <p className="muted">{t("empty.hint")}</p>
        </main>
      )}

      <footer className="footer">
        <span>{t("footer")}</span>
      </footer>

      <InvoiceDrawer
        payload={drawer}
        findings={batch?.findings ?? []}
        graph={batch?.graph ?? null}
        onClose={() => setDrawer(null)}
        onFocusGraphNode={handleFocusGraphNode}
      />
    </div>
  );
}
