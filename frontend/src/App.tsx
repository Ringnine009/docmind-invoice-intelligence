import { useEffect, useState } from "react";
import { getBatch, loadDemo, uploadInvoices } from "./api";
import type { Batch } from "./types";
import { AuditPanel } from "./components/AuditPanel";
import { GraphView } from "./components/GraphView";
import { ResultsTable } from "./components/ResultsTable";
import { UploadZone } from "./components/UploadZone";

type Tab = "results" | "audit" | "graph";

export default function App() {
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [tab, setTab] = useState<Tab>("results");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
  }, [batchId]);

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

  const running = batch?.status === "pending" || batch?.status === "running";
  const progress = batch && batch.total > 0 ? Math.round((batch.done / batch.total) * 100) : 0;
  const succeeded = batch?.results.filter((r) => r?.success).length ?? 0;
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
              {succeeded}/{batch.total} extracted · {findingsTotal} findings
            </span>
          )}
        </section>
      )}

      {batch && (
        <>
          <nav className="tabs">
            {(["results", "audit", "graph"] as Tab[]).map((t) => (
              <button
                key={t}
                className={`tab ${tab === t ? "active" : ""}`}
                onClick={() => setTab(t)}
              >
                {t === "results" ? `Results (${batch.total})` : t === "audit" ? `Audit (${findingsTotal})` : "Graph"}
              </button>
            ))}
            <div className="tabs-spacer" />
            {batch.status === "done" && (
              <div className="export-group">
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
            {tab === "results" && <ResultsTable results={batch.results} />}
            {tab === "audit" && (
              <AuditPanel findings={batch.findings} summary={batch.audit_summary} />
            )}
            {tab === "graph" && <GraphView batchId={batch.id} batch={batch} />}
          </main>
        </>
      )}

      {!batch && (
        <main className="content empty">
          <p>Upload invoice PDFs or load the synthetic demo batch to get started.</p>
          <p className="muted">
            Extraction uses a Qwen vision model (DashScope); the audit engine then checks
            duplicates, arithmetic, tax rates and party information.
          </p>
        </main>
      )}

      <footer className="footer">
        <span>DocMind · synthetic sample data only · no real PII</span>
      </footer>
    </div>
  );
}
