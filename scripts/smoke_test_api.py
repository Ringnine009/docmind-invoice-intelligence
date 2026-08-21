#!/usr/bin/env python3
"""Optional real-API smoke test for the DashScope vision extractor.

Requires a configured DASHSCOPE_API_KEY (see .env / .env.example). Runs one
invoice through the primary model (and the fallback if the primary fails) and
prints the normalized result.

Usage:
    python scripts/smoke_test_api.py [path/to/invoice.pdf]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extraction.dashscope_extractor import DashScopeExtractor  # noqa: E402


def main() -> int:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "samples" / "invoice_001.pdf"
    if not pdf.is_file():
        print(f"file not found: {pdf}")
        return 2

    extractor = DashScopeExtractor()
    print(f"extracting {pdf} with primary model "
          f"'{extractor.settings.vision_model_primary}' ...")
    try:
        doc = extractor.extract(pdf)
    except Exception as exc:
        print(f"extraction FAILED: {exc}")
        return 1

    payload = doc.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\nOK — extraction succeeded")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
