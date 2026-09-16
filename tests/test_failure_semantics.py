"""Batch failure semantics: a failed extraction must never look like a clean audit.

Real-world symptom this pins down: with every document failing to extract, the
batch reported ``status=done``, ``done=1/1`` and ``errors=[]``, and
``/audit`` answered ``{"findings": []}`` with a normal-looking status. A
reviewer (or a downstream system) reads that as "this batch is clean" — the
most dangerous false negative an audit product can produce. The cause was
``done = sum(1 for r in results if r is not None)``: a *failed* result is still
a non-None object, so failures were counted as completions.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.extraction.base import ExtractionError
from app.services.extraction.mock_extractor import MockExtractor


class _AlwaysFailExtractor(MockExtractor):
    """Every extraction raises — the all-documents-failed scenario."""

    def extract(self, file_path):
        raise ExtractionError("simulated hard failure")


class _FlakyExtractor(MockExtractor):
    """Fails the first extraction, then behaves like the mock."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def extract(self, file_path):
        if not self.failed:
            self.failed = True
            raise ExtractionError("simulated transient failure")
        return super().extract(file_path)


TERMINAL = {"done", "failed"}


def _wait_terminal(client: TestClient, batch_id: str, tries: int = 60) -> dict:
    for _ in range(tries):
        batch = client.get(f"/api/batches/{batch_id}").json()
        if batch["status"] in TERMINAL:
            return batch
        time.sleep(0.05)
    raise AssertionError(f"batch stuck in {batch['status']!r}")


def _client(extractor) -> TestClient:
    app = create_app(extractor=extractor)
    return TestClient(app)


def _upload(client: TestClient, names: list[str]) -> str:
    files = [
        ("files", (name, b"%PDF-1.4 placeholder", "application/pdf"))
        for name in names
    ]
    r = client.post("/api/invoices/upload", files=files)
    assert r.status_code == 200, r.text
    return r.json()["batch_id"]


class TestAllExtractionsFail:
    @pytest.fixture()
    def batch(self):
        with _client(_AlwaysFailExtractor()) as client:
            bid = _upload(client, ["fail_a.pdf", "fail_b.pdf"])
            yield client, _wait_terminal(client, bid)

    def test_status_is_not_done(self, batch):
        _, b = batch
        assert b["status"] != "done", "an all-failed batch must not report success"
        assert b["status"] == "failed"

    def test_done_counts_only_successful_extractions(self, batch):
        _, b = batch
        assert b["done"] == 0
        assert b["total"] == 2
        assert b["failed"] == 2

    def test_errors_are_exposed_per_file(self, batch):
        _, b = batch
        assert len(b["errors"]) == 2
        for entry in b["errors"]:
            assert entry["filename"], "each failure must name its file"
            assert entry["error"], "each failure must carry the reason"

    def test_results_still_record_the_failures(self, batch):
        _, b = batch
        assert all(r is not None and r["success"] is False for r in b["results"])


class TestAuditEndpointOnAFailedBatch:
    @pytest.fixture()
    def audit(self):
        with _client(_AlwaysFailExtractor()) as client:
            bid = _upload(client, ["fail_a.pdf", "fail_b.pdf"])
            _wait_terminal(client, bid)
            r = client.get(f"/api/batches/{bid}/audit")
            assert r.status_code == 200, r.text
            yield r.json()

    def test_the_false_signal_is_gone(self, audit):
        # 0 findings is fine on its own; 0 findings + "done" + no errors is not.
        assert audit["findings"] == []
        assert audit["status"] != "done"
        assert audit["failed"] == 2
        assert audit["errors"]

    def test_audit_is_marked_inconclusive(self, audit):
        assert audit["audit_conclusive"] is False

    def test_no_documents_were_audited(self, audit):
        assert audit["audited_documents"] == 0
        assert audit["summary"] is not None  # still observable, not a 500


class TestPartialFailure:
    def test_completed_batch_reports_both_counts(self):
        with _client(_FlakyExtractor()) as client:
            bid = _upload(client, ["flaky_a.pdf", "flaky_b.pdf"])
            b = _wait_terminal(client, bid)
        # the batch itself finished (one document did extract) …
        assert b["status"] == "done"
        # … but the failure is visible instead of being folded into `done`
        assert b["done"] == 1
        assert b["failed"] == 1
        assert len(b["errors"]) == 1
        assert "transient" in b["errors"][0]["error"]

    def test_partial_batch_is_not_conclusive(self):
        with _client(_FlakyExtractor()) as client:
            bid = _upload(client, ["flaky_a.pdf", "flaky_b.pdf"])
            _wait_terminal(client, bid)
            audit = client.get(f"/api/batches/{bid}/audit").json()
        assert audit["audit_conclusive"] is False
        assert audit["audited_documents"] == 1


class TestSuccessfulBatch:
    def test_clean_batch_is_marked_conclusive(self):
        with _client(MockExtractor()) as client:
            bid = _upload(client, ["ok_a.pdf", "ok_b.pdf"])
            b = _wait_terminal(client, bid)
            audit = client.get(f"/api/batches/{bid}/audit").json()
        assert b["status"] == "done"
        assert b["done"] == 2 and b["failed"] == 0 and b["errors"] == []
        assert audit["audit_conclusive"] is True
        assert audit["audited_documents"] == 2

    def test_demo_batch_is_conclusive(self):
        with _client(MockExtractor()) as client:
            bid = client.post("/api/demo/load", json={"count": 3}).json()["batch_id"]
            audit = client.get(f"/api/batches/{bid}/audit").json()
        assert audit["audit_conclusive"] is True
        assert audit["audited_documents"] == 3
