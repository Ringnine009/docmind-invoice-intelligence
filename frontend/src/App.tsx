import { useEffect, useState } from "react";
import { getBatch, loadDemo, retryFailed, uploadInvoices } from "./api";
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
            <p>Multimodal invoice intelligence &amp; audit</p>
          </div>
        </div>
        <div className="topbar-actions">
          {batch && (
            <span className={`chip source-${batch.source}`} title={batch.source}>
              {batch.source === "demo" ? "DEMO · synthetic data" : "REAL UPLOAD"}
            </span>
          )}
          {batchId && (
            <span className="chip mono" title={batchId}>
              batch {batchId.slice(0, 8)}
            </span>
          )}
          <button className="btn ghost" onClick={handleDemo} disabled={busy}>
            {busy ? "Working…" : "Load demo (30)"}
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
          <span className={`chip status ${batch.status}`}>{batch.status}</span>
          {running ? (
            <div className="progress">
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${progress}%` }} />
              </div>
              <span className="mono">
                {batch.done}/{batch.total} · {progress}%
              </span>
            </div>
          ) : (
            <span className="mono">
              {succeeded}/{batch.total} extracted{failed > 0 ? ` · ${failed} failed` : ""} ·{" "}
              {findingsTotal} findings
            </span>
          )}
        </section>
      )}

      {batch && (
        <>
          <nav className="tabs">
            {TAB_ORDER.map((t) => {
              const disabled = t === "analysis" && batch.status !== "done";
              const label =
                t === "results"
                  ? `Results (${batch.total})`
                  : t === "audit"
                    ? `Audit (${findingsTotal})`
                    : t === "analysis"
                      ? "Analysis"
                      : "Graph";
              return (
                <button
                  key={t}
                  className={`tab ${tab === t ? "active" : ""}`}
                  disabled={disabled}
                  title={disabled ? "Analysis is available after the batch finishes" : undefined}
                  onClick={() => setTab(t)}
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
                    Retry {failed} failed
                  </button>
                )}
                <a className="btn small" href={`/api/batches/${batch.id}/export?format=csv`}>
                  Export CSV
                </a>
                <a className="btn small ghost" href={`/api/batches/${batch.id}/export?format=json`}>
                  Export JSON
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
                <p className="muted">Analysis is available once the batch finishes.</p>
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
          <p>Upload invoice PDFs or load the synthetic demo batch to get started.</p>
          <p className="muted">
            Extraction uses a Qwen vision model (DashScope); the audit engine then checks
            duplicates, arithmetic, tax rates, QR codes and party information.
          </p>
        </main>
      )}

      <footer className="footer">
        <span>DocMind · synthetic sample data only · no real PII</span>
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
