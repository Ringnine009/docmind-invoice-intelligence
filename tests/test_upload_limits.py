"""Upload size limits.

Audit finding: the upload endpoint had no size cap at all — a 3 MB PDF was
written to disk verbatim and a batch started (measured before the fix). These
tests pin a per-file cap (20 MB by default) and a per-request cap, the readable
413 that the dashboard's existing error banner shows, and the fact that a
rejected request leaves nothing behind on disk.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.batch.store import BatchStore
from app.services.extraction.mock_extractor import MockExtractor

MB = 1024 * 1024


@pytest.fixture()
def client(tmp_path):
    """Client with small limits and a throwaway data directory.

    ``model_copy`` keeps the mutation off the ``lru_cache``d global Settings
    instance that every other test in the session shares, and ``tmp_path`` keeps
    the multi-megabyte payloads out of the repository's ``data/`` directory
    (which other upload tests already fill with one placeholder file per run).
    """
    app = create_app(extractor=MockExtractor())
    app.state.extractor = MockExtractor()
    app.state.settings = app.state.settings.model_copy(
        update={
            "max_upload_mb": 2,
            "max_batch_upload_mb": 3,
            "data_dir": str(tmp_path),
        }
    )
    app.state.store = BatchStore(app.state.settings)
    with TestClient(app) as c:
        yield c


def pdf_bytes(size: int) -> bytes:
    head = b"%PDF-1.4\n"
    return head + b"A" * (size - len(head))


def uploaded_files(client: TestClient) -> list[str]:
    """Names currently present in the upload directory (state, not deltas)."""
    upload_dir = client.app.state.settings.data_path / "uploads"
    if not upload_dir.is_dir():
        return []
    return sorted(p.name for p in upload_dir.glob("*.pdf"))


class TestUploadSizeLimits:
    def test_defaults_are_the_documented_values(self):
        settings = Settings(_env_file=None)
        assert settings.max_upload_mb == 20  # per file
        assert settings.max_batch_upload_mb == 200  # per request

    def test_oversized_file_is_rejected(self, client):
        before = uploaded_files(client)
        r = client.post(
            "/api/invoices/upload",
            files=[("files", ("big.pdf", pdf_bytes(3 * MB), "application/pdf"))],
        )
        assert r.status_code == 413
        detail = r.json()["detail"]
        # The message has to be readable in the UI banner: which file, how big,
        # against which limit.
        assert "big.pdf" in detail
        assert "3.0 MB" in detail
        assert "2 MB" in detail
        assert uploaded_files(client) == before  # nothing hit the disk

    def test_file_at_the_limit_is_accepted(self, client):
        r = client.post(
            "/api/invoices/upload",
            files=[("files", ("exact.pdf", pdf_bytes(2 * MB), "application/pdf"))],
        )
        assert r.status_code == 200
        assert "exact.pdf" in uploaded_files(client)

    def test_one_oversized_file_rejects_the_whole_request(self, client):
        """A mixed batch must not be half-imported."""
        before = uploaded_files(client)
        r = client.post(
            "/api/invoices/upload",
            files=[
                ("files", ("small.pdf", pdf_bytes(MB), "application/pdf")),
                ("files", ("big.pdf", pdf_bytes(3 * MB), "application/pdf")),
            ],
        )
        assert r.status_code == 413
        assert "big.pdf" in r.json()["detail"]
        assert uploaded_files(client) == before  # small.pdf was not written either
        assert "batch_id" not in r.json()  # and no batch was created

    def test_request_total_limit_is_enforced(self, client):
        r = client.post(
            "/api/invoices/upload",
            files=[
                ("files", ("a.pdf", pdf_bytes(2 * MB), "application/pdf")),
                ("files", ("b.pdf", pdf_bytes(2 * MB), "application/pdf")),
            ],
        )
        assert r.status_code == 413
        detail = r.json()["detail"]
        assert "4.0 MB" in detail
        assert "3 MB" in detail

    def test_within_limits_still_uploads(self, client):
        r = client.post(
            "/api/invoices/upload",
            files=[("files", ("ok.pdf", pdf_bytes(512 * 1024), "application/pdf"))],
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1
