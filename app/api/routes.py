"""HTTP API routes for DocMind."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from pathlib import Path, PurePath
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


def _mb(size_bytes: float) -> float:
    return size_bytes / (1024 * 1024)


def _oversize_detail(name: str, size_bytes: int, limit_mb: int) -> str:
    """Readable 413 message: which file, how big, against which limit."""
    return (
        f"File '{name}' is {_mb(size_bytes):.1f} MB, over the "
        f"{limit_mb} MB per-file upload limit"
    )


def _declared_upload_size(pdf: UploadFile) -> Optional[int]:
    """Size of the received part in bytes, or ``None`` when it is unknown.

    The multipart parser reports it as ``UploadFile.size``; if that is missing,
    measure the spooled file instead, so the limit holds even for an
    ``UploadFile`` built without a size.
    """
    if pdf.size is not None:
        return pdf.size
    try:
        position = pdf.file.tell()
        pdf.file.seek(0, io.SEEK_END)
        size = pdf.file.tell()
        pdf.file.seek(position)
        return size
    except (OSError, ValueError):
        return None


def _summarize_results(results: list[dict | None]) -> dict:
    """Count *successful* extractions and collect the per-file failures.

    A failed extraction still stores a result dict (``success=False``); the old
    ``sum(1 for r in results if r is not None)`` therefore counted failures as
    completions, so a batch whose every document failed reported ``done=n/n``,
    ``errors=[]`` and ``status=done`` — an audit that silently looks clean.
    """
    done = sum(1 for r in results if r and r.get("success"))
    errors = [
        {
            "filename": r.get("filename"),
            "error": r.get("error") or "extraction failed",
        }
        for r in results
        if r and not r.get("success")
    ]
    return {"done": done, "failed": len(errors), "errors": errors}


def _batch_status(done: int, total: int) -> str:
    """Terminal status for a finished extraction pass.

    ``failed`` when nothing extracted at all (there is nothing to audit and
    zero findings would be a false all-clear); ``done`` when the pass ran to
    completion, with the failure count exposed separately.
    """
    if total > 0 and done == 0:
        return "failed"
    return "done"


def _run_audit_and_graph(docs: list[InvoiceDocument], settings):
    """Run the audit engine and graph builder over extracted documents.

    Returns ``(findings, summary, graph, insights, rule_errors)`` — ``rule_errors``
    names any rule that crashed, so a broken rule is visible in the batch
    instead of quietly shrinking the findings list.
    """
    engine = AuditEngine(settings)
    findings = engine.run(docs)
    findings_payload = [f.model_dump(mode="json") for f in findings]
    summary = engine.summarize(findings, documents_audited=len(docs))
    kg = GraphBuilder().build_with_insights(docs)
    return (
        findings_payload,
        summary,
        kg["graph"],
        kg["insights"],
        [error.to_dict() for error in engine.errors],
    )


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
    per_file_limit = settings.max_upload_mb * 1024 * 1024

    # Validate every file *before* writing any of them, so a rejected upload
    # leaves no half-imported batch behind in the upload directory.
    declared: list[Optional[int]] = []
    for pdf in pdfs:
        raw = pdf.filename or ""
        # Security: only a bare file name is acceptable. Reject absolute
        # paths, ".." traversal and any path with directory components —
        # the client-supplied name must never escape the upload directory.
        name = PurePath(raw.replace("\\", "/")).name
        if (
            not raw.strip()
            or not name
            or name in {".", ".."}
            or name != raw.replace("\\", "/").strip()
        ):
            raise HTTPException(status_code=400, detail=f"Unsafe filename: {raw!r}")
        target = upload_dir / name
        # Defense in depth: resolved target must stay inside the upload dir.
        if not target.resolve().is_relative_to(upload_dir.resolve()):
            raise HTTPException(status_code=400, detail=f"Unsafe filename: {raw!r}")
        size = _declared_upload_size(pdf)
        if size is not None and size > per_file_limit:
            raise HTTPException(
                status_code=413,
                detail=_oversize_detail(name, size, settings.max_upload_mb),
            )
        declared.append(size)

    total_declared = sum(size for size in declared if size is not None)
    batch_limit = settings.max_batch_upload_mb * 1024 * 1024
    if total_declared > batch_limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Batch upload is {_mb(total_declared):.1f} MB, over the "
                f"{settings.max_batch_upload_mb} MB per-request upload limit"
            ),
        )

    names: list[str] = []
    for pdf in pdfs:
        name = PurePath((pdf.filename or "").replace("\\", "/")).name
        target = upload_dir / name
        stem = Path(name).stem
        suffix_idx = 1
        while target.exists():
            target = upload_dir / f"{stem}_{suffix_idx}{Path(name).suffix}"
            suffix_idx += 1
        data = await pdf.read()
        # Defense in depth: the bytes actually received are authoritative, in
        # case the parser reported no size for this part.
        if len(data) > per_file_limit:
            raise HTTPException(
                status_code=413,
                detail=_oversize_detail(name, len(data), settings.max_upload_mb),
            )
        with target.open("wb") as out:
            out.write(data)
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
                **_summarize_results(results),
            )

        docs = [
            InvoiceDocument.model_validate(r["doc"]) for r in results if r and r.get("doc")
        ]
        findings, summary, graph, insights, rule_errors = _run_audit_and_graph(
            docs, settings
        )
        counts = _summarize_results(results)
        request.app.state.store.update(
            batch_id,
            status=_batch_status(counts["done"], batch["total"]),
            findings=findings,
            audit_summary=summary,
            graph=graph,
            insights=insights,
            rule_errors=rule_errors,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as exc:
        request.app.state.store.update(
            batch_id,
            status="failed",
            errors=[{"filename": None, "error": str(exc)}],
        )
    return _get_batch_or_404(request, batch_id)


@router.get("/api/batches/{batch_id}/audit")
async def get_audit(batch_id: str, request: Request):
    batch = _get_batch_or_404(request, batch_id)
    if batch["status"] not in {"done", "failed"}:
        raise HTTPException(status_code=409, detail="batch not finished yet")

    errors = batch.get("errors") or []
    failed = batch.get("failed", 0)
    rule_errors = batch.get("rule_errors") or []
    audited = (batch.get("audit_summary") or {}).get("documents_audited", 0)
    total = batch["total"]
    # "0 findings" only means "no anomalies" when every document actually made
    # it through extraction, the whole batch was audited and every rule ran to
    # completion. Otherwise the audit is inconclusive and the caller must be
    # told so explicitly rather than being handed an empty list that reads as
    # an all-clear.
    conclusive = (
        batch["status"] == "done"
        and total > 0
        and failed == 0
        and not errors
        and not rule_errors
        and audited == total
    )
    return {
        "findings": batch["findings"],
        "summary": batch["audit_summary"],
        "status": batch["status"],
        "total": total,
        "done": batch["done"],
        "failed": failed,
        "errors": errors,
        "rule_errors": rule_errors,
        "audited_documents": audited,
        "audit_conclusive": conclusive,
    }


@router.get("/api/batches/{batch_id}/graph")
async def get_graph(batch_id: str, request: Request):
    batch = _get_batch_or_404(request, batch_id)
    if batch["status"] not in {"done", "failed"}:
        raise HTTPException(status_code=409, detail="batch not finished yet")
    empty = {"nodes": [], "edges": [], "statistics": {}}
    return {"graph": batch["graph"] or empty, "insights": batch["insights"] or {}}


@router.get("/api/batches/{batch_id}/export")
async def export_batch(batch_id: str, request: Request, format: str = "json"):
    batch = _get_batch_or_404(request, batch_id)
    if format not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
    rows = [r for r in batch["results"] if r and r.get("doc")]

    if format == "json":
        payload = [{"filename": r["filename"], **r["doc"]} for r in rows]
        filename = f"docmind_batch_{batch_id}.json"
        # The dashboard renders both export buttons as plain `<a href>` links,
        # so a download only happens when the response carries
        # `Content-Disposition`. Returning the bare list made FastAPI serialise
        # it inline with no such header and "Export JSON" downloaded nothing.
        # `ensure_ascii=False` keeps Chinese names readable instead of \uXXXX.
        return Response(
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([col for col, _ in _EXPORT_COLUMNS])
    for r in rows:
        # `filename` lives on the result row, not inside the extracted
        # document, so resolving every column against `r["doc"]` left the
        # filename column empty. Merge it in exactly like the JSON branch.
        record = {"filename": r["filename"], **r["doc"]}
        writer.writerow([_get_doc_value(record, path) for _, path in _EXPORT_COLUMNS])
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
    findings, summary, graph, insights, rule_errors = _run_audit_and_graph(
        docs, request.app.state.settings
    )
    counts = _summarize_results(results)
    request.app.state.store.update(
        batch_id,
        status=_batch_status(counts["done"], len(files)),
        results=results,
        findings=findings,
        audit_summary=summary,
        graph=graph,
        insights=insights,
        rule_errors=rule_errors,
        completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        **counts,
    )
    return {
        "batch_id": batch_id,
        "status": _batch_status(counts["done"], len(files)),
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
                **_summarize_results(results),
            )

    try:
        await asyncio.gather(*(process(i, fn) for i, fn in enumerate(batch["files"])))
        docs = [
            InvoiceDocument.model_validate(r["doc"])
            for r in results
            if r and r.get("doc")
        ]
        findings, summary, graph, insights, rule_errors = _run_audit_and_graph(
            docs, settings
        )
        counts = _summarize_results(results)
        store.update(
            batch_id,
            status=_batch_status(counts["done"], len(batch["files"])),
            findings=findings,
            audit_summary=summary,
            graph=graph,
            insights=insights,
            rule_errors=rule_errors,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as exc:  # keep the batch observable on failure
        store.update(
            batch_id,
            status="failed",
            errors=[{"filename": None, "error": str(exc)}],
        )
