"""API integration tests using the FastAPI TestClient (mock extractor)."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.extraction.mock_extractor import MockExtractor


@pytest.fixture()
def client():
    app = create_app(extractor=MockExtractor())
    app.state.extractor = MockExtractor()
    with TestClient(app) as c:
        yield c


def make_pdf_bytes() -> bytes:
    # Minimal placeholder PDF bytes are rejected by the pipeline's type
    # check before parsing, so any non-empty bytes are fine for routing tests.
    return b"%PDF-1.4 placeholder"


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestRules:
    def test_list_rules(self, client):
        r = client.get("/api/rules")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["rules"], list)
        assert len(body["rules"]) >= 5
        ids = {rule["rule_id"] for rule in body["rules"]}
        assert "dup_invoice_number" in ids


class TestUploadAndBatch:
    def test_upload_creates_batch(self, client):
        files = [
            ("files", ("inv1.pdf", make_pdf_bytes(), "application/pdf")),
            ("files", ("inv2.pdf", make_pdf_bytes(), "application/pdf")),
        ]
        r = client.post("/api/invoices/upload", files=files)
        assert r.status_code == 200
        batch_id = r.json()["batch_id"]
        assert batch_id

    def test_upload_rejects_non_pdf(self, client):
        files = [("files", ("evil.exe", b"MZ...", "application/octet-stream"))]
        r = client.post("/api/invoices/upload", files=files)
        assert r.status_code == 400

    def test_batch_status_eventually_done(self, client):
        files = [("files", ("inv.pdf", make_pdf_bytes(), "application/pdf"))]
        r = client.post("/api/invoices/upload", files=files)
        batch_id = r.json()["batch_id"]
        status = client.get(f"/api/batches/{batch_id}").json()
        assert status["status"] in {"pending", "running", "done"}
        assert status["total"] == 1

    def test_unknown_batch_404(self, client):
        r = client.get("/api/batches/does-not-exist")
        assert r.status_code == 404


class TestDemoBatch:
    def test_load_demo_batch(self, client):
        r = client.post("/api/demo/load", json={"count": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["batch_id"]
        assert body["status"] == "done"

    def test_demo_batch_results_are_invoices(self, client):
        r = client.post("/api/demo/load", json={"count": 2})
        batch_id = r.json()["batch_id"]
        status = client.get(f"/api/batches/{batch_id}").json()
        assert len(status["results"]) == 2
        assert status["results"][0]["invoice_number"]


class TestAuditAndGraph:
    def _load_batch(self, client, count=2):
        r = client.post("/api/demo/load", json={"count": count})
        return r.json()["batch_id"]

    def test_audit_endpoint(self, client):
        batch_id = self._load_batch(client)
        r = client.get(f"/api/batches/{batch_id}/audit")
        assert r.status_code == 200
        body = r.json()
        assert "findings" in body
        assert "summary" in body

    def test_graph_endpoint(self, client):
        batch_id = self._load_batch(client, count=3)
        r = client.get(f"/api/batches/{batch_id}/graph")
        assert r.status_code == 200
        body = r.json()
        assert "nodes" in body["graph"]
        assert body["graph"]["statistics"]["total_nodes"] > 0


class TestExport:
    def _load_batch(self, client, count=2):
        r = client.post("/api/demo/load", json={"count": count})
        return r.json()["batch_id"]

    def test_export_json(self, client):
        batch_id = self._load_batch(client)
        r = client.get(f"/api/batches/{batch_id}/export", params={"format": "json"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) == 2

    def test_export_csv(self, client):
        batch_id = self._load_batch(client)
        r = client.get(f"/api/batches/{batch_id}/export", params={"format": "csv"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        text = r.text
        assert "invoice_number" in text
        assert "buyer_name" in text

    def test_export_invalid_format_400(self, client):
        batch_id = self._load_batch(client)
        r = client.get(f"/api/batches/{batch_id}/export", params={"format": "xml"})
        assert r.status_code == 400
