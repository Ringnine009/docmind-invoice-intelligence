#!/usr/bin/env python3
"""Evaluate the audit engine against the injected anomalies in the ground truth.

The field-level benchmark (``scripts/run_benchmark.py``) answers *"did we read
the invoice correctly?"*. This script answers the audit question: *"did the
rule engine flag the invoices that ``benchmark/ground_truth.json`` annotates as
anomalous?"* — and writes the answer to ``docs/audit-eval.md``.

Fully offline: the audit engine is pure Python and needs no API key, so this
costs nothing and is safe to run in CI.

    python scripts/run_audit_eval.py
    python scripts/run_audit_eval.py --limit 15      # first 15 invoices only
    python scripts/run_audit_eval.py --no-write      # print, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.eval.audit_metrics import (  # noqa: E402
    AuditEvalError,
    evaluate_ground_truth_audit,
    load_ground_truth,
    render_audit_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="first N invoices (0 = all)")
    parser.add_argument(
        "--out", type=str, default="benchmark/results/audit_eval.json"
    )
    parser.add_argument("--docs", type=str, default="docs/audit-eval.md")
    parser.add_argument("--no-write", action="store_true", help="print only")
    args = parser.parse_args()

    files = load_ground_truth()
    names = sorted(files)
    if args.limit > 0:
        names = names[: args.limit]
        files = {n: files[n] for n in names}

    print(f"audit eval: {len(names)} invoices (ground truth carries the labels)")
    try:
        report = evaluate_ground_truth_audit(files, names=names)
    except AuditEvalError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print("\n=== per anomaly class ===")
    for cls, m in report["classes"].items():
        p = "—" if m["precision"] is None else f"{m['precision']:.4f}"
        r = "—" if m["recall"] is None else f"{m['recall']:.4f}"
        print(
            f"  {cls:<22} support={m['support']:<3} tp={m['tp']:<3} "
            f"fp={m['fp']:<3} fn={m['fn']:<3} P={p} R={r}"
        )

    print("\n=== per rule ===")
    for rule_id, m in report["rules"].items():
        print(
            f"  {rule_id:<22} tp={m['tp']:<3} fp={m['fp']:<3} fn={m['fn']:<3} "
            f"P={m['precision']} R={m['recall']}"
        )

    o = report["overall"]
    print(
        f"\nOVERALL micro P={o['precision']} R={o['recall']} F1={o['f1']} "
        f"(tp={o['tp']} fp={o['fp']} fn={o['fn']}); "
        f"invoice-level exact match={o['invoice_exact_match']}"
    )
    if report["rule_errors"]:
        print(f"WARNING: {len(report['rule_errors'])} rule error(s): {report['rule_errors']}")
    if report["unmatched_selectors"]:
        print(f"WARNING: unmatched mapping selectors: {report['unmatched_selectors']}")

    if args.no_write:
        return 0

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    docs_path = REPO_ROOT / args.docs
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_audit_markdown(report), encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")
    print(f"wrote {docs_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
