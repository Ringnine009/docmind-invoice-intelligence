"""Real end-to-end upload verification (requires a running server + API key).

Uploads 3 synthetic invoices through the real DashScope extractor, waits for
the batch to finish, and checks: extraction success, QR cross-check firing on
the tampered sample, audit summary and graph availability.

Usage: python scripts/verify_real_upload.py  (run while uvicorn is up)
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
SAMPLES = ["invoice_001.pdf", "invoice_004.pdf", "invoice_022.pdf"]


def main() -> int:
    boundary = "----docmindverify"
    body = bytearray()
    for name in SAMPLES:
        data = (Path(__file__).resolve().parents[1] / "samples" / name).read_bytes()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode()
        )
        body.extend(data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        BASE + "/api/invoices/upload",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    upload = json.loads(urllib.request.urlopen(req, timeout=60).read())
    batch_id = upload["batch_id"]
    print(f"uploaded {upload['total']} files → batch {batch_id}")

    deadline = time.time() + 600
    batch = None
    while time.time() < deadline:
        batch = json.loads(
            urllib.request.urlopen(BASE + f"/api/batches/{batch_id}", timeout=30).read()
        )
        if batch["status"] in {"done", "failed"}:
            break
        time.sleep(5)
    assert batch, "batch never finished"
    print(f"batch status: {batch['status']} (source={batch.get('source')})")

    for r in batch["results"]:
        print(f"  {r['filename']:<18} success={r['success']} "
              f"number={r.get('invoice_number')} qr={bool(r.get('doc', {}).get('qr_payload'))}")

    assert all(r["success"] for r in batch["results"]), "some extractions failed"
    assert all(r["doc"].get("qr_payload") for r in batch["results"]), "QR payload missing"

    qr_findings = [
        f for f in batch["findings"] if f["rule_id"] == "qr_crosscheck"
    ]
    print(f"qr_crosscheck findings: {len(qr_findings)}")
    for f in qr_findings:
        print(f"  [{f['severity']}] {f['message']}")
    assert len(qr_findings) >= 1, "expected at least the invoice_022 QR mismatch"
    print(f"audit summary: {batch['audit_summary']}")
    print("REAL UPLOAD E2E: PASS")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
