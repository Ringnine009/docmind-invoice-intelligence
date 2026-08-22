"""HTTP API routes for DocMind."""

from __future__ import annotations

import asyncio
import csv
import io
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.models.invoice import InvoiceDocument
from app.services.audit.base import list_rule_metadata
from app.services.audit.engine import AuditEngine
from app.services.extraction.mock_extractor import MockExtractor
from app.services.graph.builder import GraphBuilder

router = APIRouter()

_EXPORT_COLUMNS = [
    ("filename", "filename"),
    ("invoice_number", "invoice_number"),
    ("issue_date", "issue_date"),
    ("buyer_name", "buyer.name"),
    ("buyer_tax_id", "buyer.tax_id"),
    ("seller_name", "seller.name"),
    ("seller_tax_id", "seller.tax_id"),
    ("amount_excluding_tax", "amount_excluding_tax"),
    ("tax_amount", "tax_amount"),
    ("amount_including_tax", "amount_including_tax"),
    ("issuer", "issuer"),
    ("check_code", "check_code"),
]


def _get_batch_or_404(request: Request, batch_id: str) -> dict:
    batch = request.app.state.store.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
    return batch


def _get_doc_value(doc: dict, path: str):
    if "." in path:
        part, rest = path.split(".", 1)
        obj = doc.get(part) or {}
        return obj.get(rest)
    return doc.get(path)


def _run_audit_and_graph(docs: list[InvoiceDocument], settings):
    """Run the audit engine and graph builder over extracted documents."""
    engine = AuditEngine(settings)
    findings = engine.run(docs)
    findings_payload = [f.model_dump(mode="json") for f in findings]
    summary = engine.summarize(findings)
    kg = GraphBuilder().build_with_insights(docs)
    return findings_payload, summary, kg["graph"], kg["insights"]


# -- health & metadata -------------------------------------------------------


@router.get("/api/health")
async def health(request: Request):
    return {"status": "ok", "service": "docmind", "version": "0.1.0"}


@router.get("/api/rules")
async def rules(request: Request):
    return {"rules": list_rule_metadata()}


# -- upload & batch lifecycle ------------------------------------------------


@router.post("/api/invoices/upload")
async def upload_invoices(request: Request, files: list[UploadFile] = File(...)):
    pdfs = [f for f in files if (f.filename or "").lower().endswith(".pdf")]
    if not pdfs:
        raise HTTPException(status_code=400, detail="No PDF files provided")
    if len(pdfs) > 200:
        raise HTTPException(status_code=400, detail="Too many files (max 200)")

    settings = request.app.state.settings
    upload_dir = settings.data_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    names: list[str] = []
    for pdf in pdfs:
        name = pdf.filename or f"invoice_{len(names)}.pdf"
        target = upload_dir / name
        stem = Path(name).stem
        suffix_idx = 1
        while target.exists():
            target = upload_dir / f"{stem}_{suffix_idx}{Path(name).suffix}"
            suffix_idx += 1
        with target.open("wb") as out:
            out.write(await pdf.read())
        names.append(target.name)

    batch_id = request.app.state.store.create(names, source="upload")
    asyncio.create_task(_run_batch(request.app, batch_id))
    return {"batch_id": batch_id, "total": len(names), "status": "pending"}


@router.get("/api/batches/{batch_id}")
async def get_batch(batch_id: str, request: Request):
    return _get_batch_or_404(request, batch_id)


class _RetryRequest(BaseModel):
    indices: list[int]


@router.post("/api/batches/{batch_id}/retry")
async def retry_batch(batch_id: str, payload: _RetryRequest, request: Request):
    """Re-extract specific (failed) files of a finished batch, then re-audit."""
    batch = _get_batch_or_404(request, batch_id)
    if batch["status"] in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="batch still running")
    indices = sorted(set(payload.indices))
    if not indices:
        raise HTTPException(status_code=400, detail="no indices provided")
    if any(i < 0 or i >= batch["total"] for i in indices):
        raise HTTPException(status_code=400, detail="index out of range")

    extractor = (
        MockExtractor()
        if batch.get("source") == "demo"
        else request.app.state.extractor
    )
    settings = request.app.state.settings
    upload_dir = settings.data_path / "uploads"

    request.app.state.store.update(batch_id, status="running")
    results = list(batch["results"])
    try:
        for idx in indices:
            filename = batch["files"][idx]
            path = upload_dir / filename if batch.get("source") != "demo" else filename
            try:
                doc = await asyncio.to_thread(extractor.extract, path)
                results[idx] = {
                    "filename": filename,
                    "success": True,
                    "invoice_number": doc.invoice_number,
                    "doc": doc.model_dump(mode="json"),
                    "error": None,
                }
            except Exception as exc:
                results[idx] = {
                    "filename": filename,
                    "success": False,
                    "invoice_number": None,
                    "doc": None,
                    "error": str(exc),
                }
            request.app.state.store.update(
                batch_id,
                results=list(results),
                done=sum(1 for r in results if r is not None),
            )

        docs = [
            InvoiceDocument.model_validate(r["doc"]) for r in results if r and r.get("doc")
        ]
        findings, summary, graph, insights = _run_audit_and_graph(docs, settings)
        request.app.state.store.update(
            batch_id,
            status="done",
            findings=findings,
            audit_summary=summary,
            graph=graph,
            insights=insights,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as exc:
        request.app.state.store.update(batch_id, status="failed", errors=[str(exc)])
    return _get_batch_or_404(request, batch_id)


@router.get("/api/batches/{batch_id}/audit")
async def get_audit(batch_id: str, request: Request):
    batch = _get_batch_or_404(request, batch_id)
    if batch["status"] not in {"done", "failed"}:
        raise HTTPException(status_code=409, detail="batch not finished yet")
    return {"findings": batch["findings"], "summary": batch["audit_summary"]}


@router.get("/api/batches/{batch_id}/graph")
async def get_graph(batch_id: str, request: Request):
    batch = _get_batch_or_404(request, batch_id)
    if batch["status"] not in {"done", "failed"}:
        raise HTTPException(status_code=409, detail="batch not finished yet")
    empty = {"nodes": [], "edges": [], "statistics": {}}
    return {"graph": batch["graph"] or empty, "insights": batch["insights"] or {}}


@router.get("/api/batches/{batch_id}/export")
async def export_batch(batch_id: str, format: str = "json", request: Request = None):
    batch = _get_batch_or_404(request, batch_id)
    if format not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
    rows = [r for r in batch["results"] if r and r.get("doc")]

    if format == "json":
        return [{"filename": r["filename"], **r["doc"]} for r in rows]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([col for col, _ in _EXPORT_COLUMNS])
    for r in rows:
        doc = r["doc"]
        writer.writerow(
            [_get_doc_value(doc, path) if doc else "" for _, path in _EXPORT_COLUMNS]
        )
    filename = f"docmind_batch_{batch_id}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -- demo mode (offline: mock extractor over the synthetic sample set) -------


@router.post("/api/demo/load")
async def load_demo(request: Request, count: Optional[int] = Body(default=10, embed=True)):
    count = max(1, min(count or 10, 60))
    extractor = MockExtractor()
    if not extractor._ground_truth:
        raise HTTPException(
            status_code=500,
            detail="no ground truth available — run scripts/generate_synthetic_invoices.py first",
        )

    files = sorted(extractor._ground_truth.keys())[:count]
    batch_id = request.app.state.store.create(files, source="demo")
    results: list[dict] = []
    for filename in files:
        try:
            doc = extractor.extract(filename)
            results.append(
                {
                    "filename": filename,
                    "success": True,
                    "invoice_number": doc.invoice_number,
                    "doc": doc.model_dump(mode="json"),
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "filename": filename,
                    "success": False,
                    "invoice_number": None,
                    "doc": None,
                    "error": str(exc),
                }
            )

    docs = [
        InvoiceDocument.model_validate(r["doc"]) for r in results if r.get("doc")
    ]
    findings, summary, graph, insights = _run_audit_and_graph(
        docs, request.app.state.settings
    )
    request.app.state.store.update(
        batch_id,
        status="done",
        done=len(results),
        results=results,
        findings=findings,
        audit_summary=summary,
        graph=graph,
        insights=insights,
        completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    return {
        "batch_id": batch_id,
        "status": "done",
        "total": len(files),
        "findings_count": len(findings),
        "graph_nodes": graph["statistics"]["total_nodes"],
    }


# -- internal ----------------------------------------------------------------


async def _run_batch(app, batch_id: str) -> None:
    """Extract → audit → graph for an uploaded batch (background task)."""
    store = app.state.store
    batch = store.get(batch_id)
    if batch is None:
        return
    store.update(batch_id, status="running")

    settings = app.state.settings
    extractor = app.state.extractor
    upload_dir = settings.data_path / "uploads"
    sem = asyncio.Semaphore(max(1, settings.max_workers))
    results: list[dict | None] = [None] * len(batch["files"])

    async def process(idx: int, filename: str) -> None:
        async with sem:
            path = upload_dir / filename

            def _extract():
                return extractor.extract(path)

            try:
                doc = await asyncio.to_thread(_extract)
                results[idx] = {
                    "filename": filename,
                    "success": True,
                    "invoice_number": doc.invoice_number,
                    "doc": doc.model_dump(mode="json"),
                    "error": None,
                }
            except Exception as exc:
                results[idx] = {
                    "filename": filename,
                    "success": False,
                    "invoice_number": None,
                    "doc": None,
                    "error": str(exc),
                }
            store.update(
                batch_id,
                results=list(results),
                done=sum(1 for r in results if r is not None),
            )

    try:
        await asyncio.gather(*(process(i, fn) for i, fn in enumerate(batch["files"])))
        docs = [
            InvoiceDocument.model_validate(r["doc"])
            for r in results
            if r and r.get("doc")
        ]
        findings, summary, graph, insights = _run_audit_and_graph(docs, settings)
        store.update(
            batch_id,
            status="done",
            findings=findings,
            audit_summary=summary,
            graph=graph,
            insights=insights,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as exc:  # keep the batch observable on failure
        store.update(batch_id, status="failed", errors=[str(exc)])
