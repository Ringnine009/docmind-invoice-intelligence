#!/usr/bin/env python3
"""End-to-end evaluation: real vision extraction → audit → score.

The extracted-input question the other evaluation pages leave open: *does the
audit engine still flag the right invoices when the fields come from the vision
model instead of from the labels?*

    # real API, 2 rounds over all 30 samples, ¥25 hard budget
    python scripts/run_e2e_eval.py --rounds 2

    # harness sanity check, no network and no cost
    python scripts/run_e2e_eval.py --extractor mock --rounds 2

    # cheap probe
    python scripts/run_e2e_eval.py --rounds 1 --limit 3 --budget-cny 0.5

Writes ``benchmark/results/e2e_eval.json`` (machine-readable, every round's raw
numbers) and ``docs/e2e-eval.md`` (the readable report).

Cost accounting is per billed response, recorded through the extractor's usage
hook before JSON parsing — a call that returns tokens and then fails to parse
is still charged. The run aborts as soon as the accumulated list-price cost
exceeds ``--budget-cny``.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.models.invoice import InvoiceDocument  # noqa: E402
from app.services.audit.engine import AuditEngine  # noqa: E402
from app.services.eval.audit_metrics import (  # noqa: E402
    evaluate_audit,
    evaluate_ground_truth_audit,
    load_ground_truth,
)
from app.services.eval.e2e import (  # noqa: E402
    PRICING,
    SCHEMA_VERSION,
    BudgetExceeded,
    UsageMeter,
    classify_divergences,
    derive_full_batch_audit,
    micro_from_audit,
    redact_endpoint,
    render_e2e_markdown,
    summarize_rounds,
)
from app.services.eval.metrics import (  # noqa: E402
    compare_documents,
    field_accuracy_report,
)
from app.services.extraction.mock_extractor import MockExtractor  # noqa: E402

#: Field paths stored per invoice in the JSON artifact (kept compact).
_FIELDS = (
    "invoice_number",
    "issue_date",
    "buyer.name",
    "buyer.tax_id",
    "seller.name",
    "seller.tax_id",
    "amount_excluding_tax",
    "tax_amount",
    "amount_including_tax",
)


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.settings = get_settings()
        self.gt_files = load_ground_truth()
        self.names = sorted(self.gt_files)
        if args.limit > 0:
            self.names = self.names[: args.limit]
        self.gt_docs = {
            name: InvoiceDocument.model_validate(self.gt_files[name])
            for name in self.names
        }
        self.meter = UsageMeter(budget_cny=args.budget_cny)
        self.tolerance = args.tolerance
        self._local = threading.local()
        self._outcome_lock = threading.Lock()
        self._outcomes: list[bool] = []
        self.aborted: dict | None = None

    # -- extractor construction --------------------------------------------

    def _extractor_factory(self):
        if self.args.extractor == "mock":
            return lambda: MockExtractor(REPO_ROOT / "benchmark" / "ground_truth.json")
        if self.args.extractor == "dashscope":
            from app.services.extraction.dashscope_extractor import DashScopeExtractor

            recorder = self.meter.recorder()
            return lambda: DashScopeExtractor(self.settings, usage_recorder=recorder)
        raise SystemExit(f"unknown extractor {self.args.extractor!r}")

    def _thread_extractor(self, factory):
        ex = getattr(self._local, "extractor", None)
        if ex is None:
            ex = factory()
            self._local.extractor = ex
        return ex

    # -- one round ----------------------------------------------------------

    def run_round(self, index: int, factory) -> dict:
        started = time.time()
        cost_before = self.meter.total_cost_cny
        calls_before = self.meter.n_calls
        results: dict[str, dict] = {}
        lock = threading.Lock()

        def process(name: str) -> None:
            if self.aborted:
                return
            pdf_path = REPO_ROOT / "samples" / name
            extractor = self._thread_extractor(factory)
            t0 = time.time()
            try:
                doc = extractor.extract(pdf_path)
                outcome = {"file": name, "ok": True, "doc": doc, "error": None}
            except Exception as exc:  # noqa: BLE001 — reported, never hidden
                outcome = {
                    "file": name,
                    "ok": False,
                    "doc": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            outcome["latency_s"] = round(time.time() - t0, 3)
            with lock:
                results[name] = outcome
                print(
                    f"  [{index}.{len(results):02d}/{len(self.names)}] {name}: "
                    f"{'ok' if outcome['ok'] else 'FAIL'} "
                    f"({outcome['latency_s']}s, ¥{self.meter.total_cost_cny:.4f})",
                    flush=True,
                )
                self._check_abort(outcome["ok"])

        workers = max(1, self.args.workers)
        if workers == 1:
            for name in self.names:
                process(name)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(process, self.names))

        elapsed = time.time() - started
        ok_names = [n for n in self.names if results.get(n, {}).get("ok")]
        failures = [
            {"file": n, "error": results[n]["error"]}
            for n in self.names
            if n in results and not results[n]["ok"]
        ]
        missing = [n for n in self.names if n not in results]

        pred_docs = [results[n]["doc"] for n in ok_names]
        gt_docs = [self.gt_docs[n] for n in ok_names]

        # -- field-level scoring -------------------------------------------
        per_invoice = []
        correct = compared = 0
        field_totals: dict[str, dict[str, int]] = {
            f: {"correct": 0, "compared": 0} for f in _FIELDS
        }
        conf_totals: dict[str, float] = {f: 0.0 for f in _FIELDS}
        for name, pred_doc in zip(ok_names, pred_docs):
            comp = compare_documents(self.gt_docs[name], pred_doc, self.tolerance)
            row = {}
            for field, detail in comp.items():
                row[field] = {
                    "match": bool(detail["match"]),
                    "compared": bool(detail["compared"]),
                    "confidence": detail["confidence"],
                    "gt": detail["gt"],
                    "pred": detail["pred"],
                }
                if field in field_totals:
                    if detail["compared"]:
                        field_totals[field]["compared"] += 1
                        field_totals[field]["correct"] += int(detail["match"])
                    conf_totals[field] += detail["confidence"] or 0.0
            per_invoice.append({"file": name, "fields": row})
            for field, totals in field_totals.items():
                if row[field]["compared"]:
                    correct += int(row[field]["match"])
                    compared += 1

        n_docs = len(ok_names)
        field_report: dict = {}
        for field in _FIELDS:
            totals = field_totals[field]
            field_report[field] = {
                "accuracy": (
                    round(totals["correct"] / totals["compared"], 4)
                    if totals["compared"]
                    else 1.0
                ),
                "correct": totals["correct"],
                "compared": totals["compared"],
                "avg_confidence": round(conf_totals[field] / n_docs, 4) if n_docs else 1.0,
            }
        field_report["overall"] = {
            "accuracy": round(correct / compared, 4) if compared else 1.0,
            "fields": len(_FIELDS),
            "correct": correct,
            "compared": compared,
        }
        field_report["_meta"] = {"tolerance": self.tolerance}

        # The same scoring, but with a failed extraction represented by an empty
        # document — the convention `scripts/run_benchmark.py` uses, and the only
        # one that is comparable to the recorded 0.8249 baseline. Reporting only
        # the auditable-subset figure would make the pipeline look better than
        # the batch-level reality.
        aligned_gt: list[InvoiceDocument] = []
        aligned_pred: list[InvoiceDocument] = []
        for name in self.names:
            aligned_gt.append(self.gt_docs[name])
            outcome = results.get(name)
            aligned_pred.append(
                outcome["doc"] if outcome and outcome["ok"] else InvoiceDocument()
            )
        field_report_allin = field_accuracy_report(
            aligned_gt, aligned_pred, self.tolerance
        )

        # -- audit scoring, both inputs ------------------------------------
        engine = AuditEngine(self.settings)
        e2e_findings = engine.run(pred_docs)
        # Resolve batch-level findings (dup_invoice_number) against the numbers
        # the engine actually saw, not the ground-truth ones — otherwise a
        # misread number turns a genuine finding into an unattributed one and
        # the misreading is scored as an audit miss.
        extracted_numbers = {
            name: (results[name]["doc"].invoice_number or None) for name in ok_names
        }
        audit_e2e = evaluate_audit(
            self.gt_files, e2e_findings, names=ok_names, numbers=extracted_numbers
        )
        audit_e2e["rule_errors"] = [e.to_dict() for e in engine.errors]
        audit_e2e_full = derive_full_batch_audit(
            audit_e2e,
            self.gt_files,
            all_names=self.names,
            audited_names=ok_names,
        )
        divergences = classify_divergences(
            self.gt_files,
            gt_audit=self.gt_audit,
            e2e_audit=audit_e2e,
            names=ok_names,
            gt_docs=gt_docs,
            pred_docs=pred_docs,
            findings=e2e_findings,
            extraction_failures=[f["file"] for f in failures],
            tolerance=self.tolerance,
        )

        return {
            "round": index,
            "elapsed_s": round(elapsed, 2),
            "workers": workers,
            "attempted": len(self.names),
            "extracted_names": ok_names,
            "extraction_failures": failures,
            "not_attempted": missing,
            "field_report": field_report,
            "field_report_allin": field_report_allin,
            "per_invoice": per_invoice,
            "audit_gt": self.gt_audit,
            "audit_e2e": audit_e2e,
            "audit_e2e_full": audit_e2e_full,
            "divergences": divergences,
            "findings_total": len(e2e_findings),
            "cost_cny": round(self.meter.total_cost_cny - cost_before, 6),
            "calls": self.meter.n_calls - calls_before,
            "mean_invoice_seconds": (
                round(
                    sum(results[n]["latency_s"] for n in self.names if n in results)
                    / max(1, len(results)),
                    2,
                )
                if results
                else None
            ),
        }

    # -- abort handling -----------------------------------------------------

    def _check_abort(self, ok: bool) -> None:
        self._outcomes.append(ok)
        if self.aborted:
            return
        try:
            self.meter.check_budget()
        except BudgetExceeded as exc:
            self.aborted = {"reason": str(exc), "kind": "budget", "round": None}
            print(f"\n!! ABORT: {exc}", file=sys.stderr, flush=True)
            return
        limit = self.args.max_consecutive_failures
        if limit and len(self._outcomes) >= limit:
            tail = self._outcomes[-limit:]
            if not any(tail):
                self.aborted = {
                    "reason": (
                        f"{limit} consecutive extraction failures — the API is "
                        "failing systematically (quota, credential or outage), "
                        "so continuing would spend time without producing data"
                    ),
                    "kind": "consecutive_failures",
                    "round": None,
                }
                print(f"\n!! ABORT: {self.aborted['reason']}", file=sys.stderr, flush=True)

    # -- main ---------------------------------------------------------------

    def run(self) -> dict:
        factory = self._extractor_factory()
        model_line = (
            f"{self.settings.vision_model_primary} / "
            f"{self.settings.vision_model_fallback}"
            if self.args.extractor == "dashscope"
            else "n/a (mock extractor)"
        )
        models = (
            {
                "primary": self.settings.vision_model_primary,
                "fallback": self.settings.vision_model_fallback,
                "label": model_line,
            }
            if self.args.extractor == "dashscope"
            else {"primary": "mock", "fallback": None, "label": model_line}
        )
        print(
            f"e2e eval: {len(self.names)} invoices x {self.args.rounds} round(s), "
            f"extractor={self.args.extractor} [{model_line}], "
            f"workers={self.args.workers}, budget=¥{self.args.budget_cny}"
        )

        # The labelled-field baseline costs nothing and never varies, so it is
        # computed once rather than per round.
        self.gt_audit = evaluate_ground_truth_audit(
            {n: self.gt_files[n] for n in self.names}, names=self.names
        )
        print(
            "labelled-field baseline: "
            f"P={self.gt_audit['overall']['precision']} "
            f"R={self.gt_audit['overall']['recall']} "
            f"F1={self.gt_audit['overall']['f1']}"
        )

        rounds: list[dict] = []
        self.models = models
        wall_start = time.time()
        for i in range(1, self.args.rounds + 1):
            if self.aborted:
                break
            print(f"\n=== round {i} ===")
            rnd = self.run_round(i, factory)
            rounds.append(rnd)
            if self.aborted:
                self.aborted["round"] = i
        wall_seconds = time.time() - wall_start

        return self.build_report(rounds, wall_seconds, model_line)

    def build_report(self, rounds: list[dict], wall_seconds: float, model_line: str) -> dict:
        attempts = sum(len(r["extracted_names"]) + len(r["extraction_failures"]) for r in rounds)
        extracted = sum(len(r["extracted_names"]) for r in rounds)
        failures = sum(len(r["extraction_failures"]) for r in rounds)
        cost = self.meter.summary()
        per_invoice_cost = (cost["total_cost_cny"] / attempts) if attempts else None
        rates = [
            r["mean_invoice_seconds"]
            for r in rounds
            if r.get("mean_invoice_seconds") is not None
        ]
        target = 100_000
        extrapolation = None
        # The mock extractor is a harness check, not a cost or latency
        # measurement: extrapolating its numbers would be meaningless.
        if self.args.extractor == "dashscope" and attempts and per_invoice_cost is not None:
            extrapolation = {
                "invoices": target,
                "basis": (
                    f"linear in measured ¥{per_invoice_cost:.6f}/attempt and "
                    f"{extracted}/{wall_seconds:.0f}s wall clock "
                    f"at {self.args.workers} workers"
                ),
                "cost_cny": round(per_invoice_cost * target, 2),
                "failure_rate": round(failures / attempts, 4),
                "failed_invoices": round(target * failures / attempts, 0),
                "wall_clock_hours": (
                    round(target / (extracted / wall_seconds) / 3600, 1)
                    if extracted and wall_seconds
                    else None
                ),
                "workers": self.args.workers,
            }

        summary = summarize_rounds(rounds) if rounds else {
            "n_rounds": 0,
            "variance_measurable": False,
            "field_accuracy": {"overall": {}, "per_field": {}},
            "audit_micro": {},
            "extraction_failures": {"total": failures},
        }

        pooled: dict[str, dict] = {}
        for key, round_key in (
            ("labelled", "audit_gt"),
            ("extracted_subset", "audit_e2e"),
            ("full_batch", "audit_e2e_full"),
        ):
            tp = fp = fn = 0
            for rnd in rounds:
                micro = micro_from_audit(rnd.get(round_key) or {})
                tp += micro["tp"]
                fp += micro["fp"]
                fn += micro["fn"]
            pooled[key] = _prf(tp, fp, fn)
        if not rounds:
            gt = micro_from_audit(self.gt_audit or {})
            pooled["labelled"] = gt

        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "extractor": self.args.extractor,
            "models": self.models,
            "endpoint_host": (
                redact_endpoint(self.settings.dashscope_openai_compat_url)
                if self.args.extractor == "dashscope"
                else None
            ),
            "n_invoices": len(self.names),
            "file_order": self.names,
            "rounds_requested": self.args.rounds,
            "rounds_completed": len(rounds),
            "aborted": self.aborted,
            "tolerance": self.tolerance,
            "render_dpi": self.settings.pdf_render_dpi,
            "workers": self.args.workers,
            "budget_cny": self.args.budget_cny,
            "pricing": {k: v.to_dict() for k, v in PRICING.items()},
            "gt_audit": self.gt_audit,
            "rounds": rounds,
            "summary": summary,
            "comparison": pooled,
            "comparison_basis": (
                "micro counts pooled over all completed rounds "
                f"({len(rounds)} round(s) x up to {len(self.names)} invoices)"
            ),
            "cost": cost,
            "latency": {
                "total_seconds": round(wall_seconds, 2),
                "mean_invoice_seconds": (
                    round(sum(rates) / len(rates), 2) if rates else None
                ),
                "per_round_seconds": [r["elapsed_s"] for r in rounds],
                "invoices_per_minute": (
                    round(extracted / wall_seconds * 60, 2)
                    if wall_seconds and extracted
                    else None
                ),
                "workers": self.args.workers,
            },
            "extrapolation": extrapolation,
            "totals": {
                "attempts": attempts,
                "extracted": extracted,
                "extraction_failures": failures,
                "findings_emitted": sum(r["findings_total"] for r in rounds),
            },
        }


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    else:
        f1 = None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extractor", choices=["dashscope", "mock"], default="dashscope")
    parser.add_argument("--rounds", type=int, default=2, help="repeat the batch N times")
    parser.add_argument("--limit", type=int, default=0, help="first N invoices (0 = all)")
    parser.add_argument(
        "--budget-cny", type=float, default=25.0, help="hard spend ceiling in CNY"
    )
    parser.add_argument("--workers", type=int, default=4, help="concurrent extractions")
    parser.add_argument("--tolerance", type=float, default=0.02, help="numeric tolerance")
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=10,
        help="abort after N consecutive extraction failures (0 = never)",
    )
    parser.add_argument("--out", type=str, default="benchmark/results/e2e_eval.json")
    parser.add_argument("--docs", type=str, default="docs/e2e-eval.md")
    parser.add_argument("--no-write", action="store_true", help="print only")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help=(
            "re-render --docs from the existing --out artifact without calling "
            "the API (zero cost); use after editing the report template"
        ),
    )
    args = parser.parse_args()

    if args.render_only:
        src = REPO_ROOT / args.out
        if not src.is_file():
            print(f"no artifact at {src} — run the evaluation first", file=sys.stderr)
            return 1
        report = json.loads(src.read_text(encoding="utf-8"))
        docs_path = REPO_ROOT / args.docs
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        docs_path.write_text(render_e2e_markdown(report), encoding="utf-8")
        print(
            f"re-rendered {docs_path.relative_to(REPO_ROOT)} from "
            f"{src.relative_to(REPO_ROOT)} (no API calls, no cost)"
        )
        return 0

    runner = Runner(args)
    report = runner.run()

    print("\n=== end-to-end vs labelled fields (micro, pooled) ===")
    for key, label in (
        ("labelled", "labelled field values"),
        ("extracted_subset", "real extraction, auditable subset"),
        ("full_batch", "real extraction, failures = misses"),
    ):
        m = report["comparison"].get(key) or {}
        print(
            f"  {label:<40} P={m.get('precision')} R={m.get('recall')} "
            f"F1={m.get('f1')} (tp={m.get('tp')} fp={m.get('fp')} fn={m.get('fn')})"
        )

    print("\n=== per round ===")
    for rnd in report["rounds"]:
        f = rnd["field_report"]["overall"]["accuracy"]
        print(
            f"  round {rnd['round']}: field acc={f} "
            f"({rnd['field_report']['overall']['correct']}/"
            f"{rnd['field_report']['overall']['compared']}), "
            f"failures={len(rnd['extraction_failures'])}, "
            f"masked={len(rnd['divergences']['masked'])}, "
            f"manufactured={len(rnd['divergences']['manufactured'])}, "
            f"unauditable={len(rnd['divergences']['unauditable'])}, "
            f"elapsed={rnd['elapsed_s']}s, cost=¥{rnd['cost_cny']}"
        )

    cost = report["cost"]
    print(
        f"\ncost: ¥{cost['total_cost_cny']:.4f} list price over {cost['n_calls']} "
        f"call(s) ({cost['total_prompt_tokens']} in / "
        f"{cost['total_completion_tokens']} out tokens), "
        f"budget ¥{cost['budget_cny']}"
    )
    print(
        f"latency: {report['latency']['total_seconds']}s wall, "
        f"{report['latency']['mean_invoice_seconds']}s mean/invoice, "
        f"{report['latency']['invoices_per_minute']} invoices/min"
    )
    if report["aborted"]:
        print(f"\nABORTED: {report['aborted']['reason']}", file=sys.stderr)

    if args.no_write:
        return 0

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    docs_path = REPO_ROOT / args.docs
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_e2e_markdown(report), encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")
    print(f"wrote {docs_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
