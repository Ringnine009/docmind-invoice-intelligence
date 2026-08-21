"""Deterministic extractor used for offline tests and the demo mode.

Reads the benchmark ground-truth file (keyed by file name); any file not
present in the ground truth receives a deterministic synthetic document, so
the pipeline is fully testable without any network or API credentials.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.config import REPO_ROOT
from app.models.invoice import InvoiceDocument, InvoiceParty
from app.services.extraction.base import Extractor
from app.services.extraction.json_utils import normalize_raw_invoice


class MockExtractor(Extractor):
    name = "mock"

    def __init__(self, ground_truth_path: str | Path | None = None) -> None:
        self.ground_truth_path = Path(ground_truth_path) if ground_truth_path else (
            REPO_ROOT / "benchmark" / "ground_truth.json"
        )
        self._ground_truth = self._load_ground_truth()

    def _load_ground_truth(self) -> dict:
        if not self.ground_truth_path.is_file():
            return {}
        try:
            data = json.loads(self.ground_truth_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            files = data.get("files")
            if isinstance(files, dict):
                return files
        return {}

    def extract(self, file_path: str | Path) -> InvoiceDocument:
        name = Path(file_path).name
        entry = self._ground_truth.get(name) or self._ground_truth.get(str(file_path))
        if isinstance(entry, dict) and entry:
            return normalize_raw_invoice(entry)

        digits = re.findall(r"\d+", name)
        number = (digits[0] if digits else "0").zfill(20)[:20]
        return InvoiceDocument(
            invoice_number=number,
            buyer=InvoiceParty(name="Mock Buyer Co."),
            seller=InvoiceParty(name="Mock Seller Co."),
            amount_including_tax=100.0,
            confidence={"invoice_number": 1.0, "amount_including_tax": 1.0},
        )
