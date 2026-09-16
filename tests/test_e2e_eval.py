"""End-to-end evaluation harness tests (offline — no API calls).

Covers the pieces that turn a real extraction run into publishable numbers:
token/cost accounting with a budget fuse, per-round aggregation, the
ground-truth-vs-extracted divergence classifier, and the usage hook added to
the DashScope extractor.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.models.invoice import InvoiceDocument, InvoiceParty
from app.services.eval.e2e import (
    DEFAULT_PRICE,
    PRICING,
    BudgetExceeded,
    UsageMeter,
    classify_divergences,
    consistency_verdict,
    derive_full_batch_audit,
    driving_field_values,
    field_deltas,
    price_for,
    redact_endpoint,
    render_e2e_markdown,
    summarize_rounds,
)
from app.services.extraction.dashscope_extractor import _EXTRACTION_PROMPT, DashScopeExtractor

from conftest import make_invoice


# --- fixtures --------------------------------------------------------------


def _audit(classes: dict[str, dict], n_invoices: int = 30) -> dict:
    """Minimal stand-in for an ``evaluate_audit`` report."""
    out = {}
    for cls, spec in classes.items():
        tp = set(spec.get("tp", ()))
        fp = set(spec.get("fp", ()))
        fn = set(spec.get("fn", ()))
        out[cls] = {
            "tp": len(tp),
            "fp": len(fp),
            "fn": len(fn),
            "tp_invoices": sorted(tp),
            "fp_invoices": sorted(fp),
            "fn_invoices": sorted(fn),
            "support": len(tp | fn),
        }
    return {"classes": out, "overall": {"n_invoices": n_invoices}}


def _field_report(values: dict[str, float], overall: float) -> dict:
    report = {
        field: {"accuracy": acc, "correct": 0, "compared": 0, "avg_confidence": 0.0}
        for field, acc in values.items()
    }
    report["overall"] = {"accuracy": overall, "fields": len(values)}
    return report


# --- pricing ---------------------------------------------------------------


class TestModelPricing:
    def test_known_model_uses_published_price(self):
        price = price_for("qwen-vl-plus")
        assert price.model == "qwen-vl-plus"
        assert price.input_cny_per_million == pytest.approx(0.8)
        assert price.output_cny_per_million == pytest.approx(2.0)
        assert price.assumed is False
        assert "aliyun" in price.source

    def test_unknown_model_falls_back_to_a_marked_assumption(self):
        price = price_for("some-model-nobody-priced")
        assert price.assumed is True
        assert price.model == "some-model-nobody-priced"

    def test_pricing_table_is_serialisable(self):
        assert "qwen-vl-plus" in PRICING
        assert DEFAULT_PRICE.assumed is True


# --- usage meter -----------------------------------------------------------


class TestUsageMeter:
    def test_totals_and_cost(self):
        meter = UsageMeter()
        meter.record("qwen-vl-plus", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert meter.total_prompt_tokens == 1_000_000
        assert meter.total_completion_tokens == 1_000_000
        # 1M input @ 0.8 + 1M output @ 2.0
        assert meter.total_cost_cny == pytest.approx(2.8)

    def test_a_failed_but_billed_call_is_still_counted(self):
        """A response that arrives and is then unparseable still costs money."""
        meter = UsageMeter()
        meter.record("qwen-vl-plus", prompt_tokens=1697, completion_tokens=1903, ok=False)
        assert meter.n_calls == 1
        assert meter.n_failed_calls == 1
        assert meter.total_cost_cny > 0

    def test_no_budget_never_trips(self):
        meter = UsageMeter()
        meter.record("qwen-vl-plus", prompt_tokens=10_000_000, completion_tokens=10_000_000)
        assert meter.over_budget is False
        meter.check_budget()  # must not raise

    def test_budget_fuse_trips_over_the_limit(self):
        meter = UsageMeter(budget_cny=0.01)
        meter.record("qwen-vl-plus", prompt_tokens=10_000, completion_tokens=10_000)
        assert meter.over_budget is True
        with pytest.raises(BudgetExceeded) as exc:
            meter.check_budget()
        assert "budget" in str(exc.value).lower()

    def test_below_budget_does_not_trip(self):
        meter = UsageMeter(budget_cny=25.0)
        meter.record("qwen-vl-plus", prompt_tokens=1000, completion_tokens=1000)
        meter.check_budget()

    def test_per_model_breakdown(self):
        meter = UsageMeter()
        meter.record("qwen-vl-plus", prompt_tokens=100, completion_tokens=200)
        meter.record("qwen3.5-ocr", prompt_tokens=1, completion_tokens=2)
        by_model = meter.by_model()
        assert set(by_model) == {"qwen-vl-plus", "qwen3.5-ocr"}
        assert by_model["qwen-vl-plus"]["prompt_tokens"] == 100
        assert by_model["qwen3.5-ocr"]["calls"] == 1

    def test_summary_is_json_safe(self):
        import json

        meter = UsageMeter(budget_cny=25.0)
        meter.record("qwen-vl-plus", prompt_tokens=1697, completion_tokens=445, latency_s=7.3)
        payload = json.dumps(meter.summary())
        assert "total_cost_cny" in payload

    def test_concurrent_recording_does_not_lose_calls(self):
        meter = UsageMeter()

        def worker():
            for _ in range(100):
                meter.record("qwen-vl-plus", prompt_tokens=1, completion_tokens=1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert meter.n_calls == 800
        assert meter.total_prompt_tokens == 800


# --- round aggregation -----------------------------------------------------


class TestSummarizeRounds:
    def test_two_rounds_report_mean_and_range(self):
        rounds = [
            {
                "field_report": _field_report({"invoice_number": 0.9}, 0.9),
                "audit_e2e": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_gt": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_e2e_full": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
            },
            {
                "field_report": _field_report({"invoice_number": 0.7}, 0.7),
                "audit_e2e": _audit({"duplicate_number": {"tp": ["a.pdf"], "fp": ["b.pdf"]}}),
                "audit_gt": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_e2e_full": _audit({"duplicate_number": {"tp": ["a.pdf"], "fp": ["b.pdf"]}}),
            },
        ]
        summary = summarize_rounds(rounds)
        assert summary["n_rounds"] == 2
        assert summary["field_accuracy"]["overall"]["mean"] == pytest.approx(0.8)
        assert summary["field_accuracy"]["overall"]["min"] == pytest.approx(0.7)
        assert summary["field_accuracy"]["overall"]["max"] == pytest.approx(0.9)
        assert summary["field_accuracy"]["overall"]["range"] == pytest.approx(0.2)
        assert summary["variance_measurable"] is True
        # micro recall differs between the two rounds -> the spread is reported
        assert summary["audit_micro"]["e2e"]["recall"]["min"] == pytest.approx(1.0)
        assert summary["audit_micro"]["e2e"]["precision"]["min"] == pytest.approx(0.5)
        assert summary["audit_micro"]["e2e"]["precision"]["max"] == pytest.approx(1.0)

    def test_single_round_declares_variance_unmeasurable(self):
        rounds = [
            {
                "field_report": _field_report({"invoice_number": 0.9}, 0.9),
                "audit_e2e": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_gt": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_e2e_full": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
            }
        ]
        summary = summarize_rounds(rounds)
        assert summary["variance_measurable"] is False
        assert summary["field_accuracy"]["overall"]["range"] is None

    def test_all_in_accuracy_is_aggregated_when_present(self):
        """Failed extractions have no fields to score, so the all-in view
        (failures counted as empty documents, the convention of the recorded
        0.8249 baseline) is reported alongside the auditable-subset view."""
        rounds = [
            {
                "field_report": _field_report({"invoice_number": 0.95}, 0.95),
                "field_report_allin": _field_report({"invoice_number": 0.8}, 0.8),
                "audit_e2e": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_gt": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_e2e_full": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
            },
            {
                "field_report": _field_report({"invoice_number": 0.93}, 0.93),
                "field_report_allin": _field_report({"invoice_number": 0.7}, 0.7),
                "audit_e2e": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_gt": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_e2e_full": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
            },
        ]
        summary = summarize_rounds(rounds)
        allin = summary["field_accuracy_allin"]
        assert allin["overall"]["mean"] == pytest.approx(0.75)
        assert allin["overall"]["range"] == pytest.approx(0.1)
        assert summary["field_accuracy"]["overall"]["mean"] == pytest.approx(0.94)

    def test_all_in_accuracy_absent_is_not_invented(self):
        rounds = [
            {
                "field_report": _field_report({"invoice_number": 0.9}, 0.9),
                "audit_e2e": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_gt": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
                "audit_e2e_full": _audit({"duplicate_number": {"tp": ["a.pdf"]}}),
            }
        ]
        assert summarize_rounds(rounds)["field_accuracy_allin"] is None

    def test_empty_rounds_raise(self):
        with pytest.raises(ValueError):
            summarize_rounds([])


# --- full-batch derivation -------------------------------------------------


class TestDeriveFullBatchAudit:
    def test_failed_invoices_become_missed_detections(self):
        gt_files = {
            "a.pdf": {"invoice_number": "1"},
            "b.pdf": {"invoice_number": "2"},
        }
        subset = _audit({"duplicate_number": {"tp": ["a.pdf"]}})
        full = derive_full_batch_audit(
            subset,
            gt_files,
            all_names=["a.pdf", "b.pdf"],
            audited_names=["a.pdf"],
            extra_labels={"b.pdf": ["duplicate_number"]},
        )
        assert full["classes"]["duplicate_number"]["tp"] == 1
        assert full["classes"]["duplicate_number"]["fn"] == 1
        assert full["classes"]["duplicate_number"]["fn_invoices"] == ["b.pdf"]

    def test_precision_is_unchanged_by_adding_missing_invoices(self):
        gt_files = {"a.pdf": {"invoice_number": "1"}, "b.pdf": {"invoice_number": "2"}}
        subset = _audit({"duplicate_number": {"tp": ["a.pdf"], "fp": ["c.pdf"]}})
        full = derive_full_batch_audit(
            subset,
            gt_files,
            all_names=["a.pdf", "b.pdf", "c.pdf"],
            audited_names=["a.pdf", "c.pdf"],
            extra_labels={"b.pdf": ["duplicate_number"]},
        )
        assert full["classes"]["duplicate_number"]["fp"] == 1
        assert full["classes"]["duplicate_number"]["precision"] == pytest.approx(0.5)


# --- driving fields / deltas ----------------------------------------------


class TestDrivingFields:
    def test_document_fields(self):
        doc = make_invoice(amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=113.0)
        values = driving_field_values(doc, ("amount_excluding_tax", "amount_including_tax"))
        assert values == {"amount_excluding_tax": 100.0, "amount_including_tax": 113.0}

    def test_item_fields_collect_every_line(self):
        from conftest import make_item

        doc = make_invoice(items=[make_item(tax_rate=13.0), make_item(tax_rate=6.0)])
        values = driving_field_values(doc, ("items.tax_rate",))
        assert values == {"items.tax_rate": [13.0, 6.0]}

    def test_missing_field_is_none(self):
        doc = InvoiceDocument()
        values = driving_field_values(doc, ("buyer.name",))
        assert values == {"buyer.name": ""}


class TestFieldDeltas:
    def test_changed_and_unchanged(self):
        gt = make_invoice(number="111", amount_including_tax=100.0)
        pred = make_invoice(number="222", amount_including_tax=100.0)
        deltas = field_deltas(gt, pred, ("invoice_number", "amount_including_tax"))
        by_field = {d["field"]: d for d in deltas}
        assert by_field["invoice_number"]["changed"] is True
        assert by_field["invoice_number"]["gt"] == "111"
        assert by_field["invoice_number"]["pred"] == "222"
        assert by_field["amount_including_tax"]["changed"] is False
        assert [d["field"] for d in deltas if d["changed"]] == ["invoice_number"]

    def test_item_deltas_survive_a_shape_change(self):
        from conftest import make_item

        gt = make_invoice(items=[make_item(tax_rate=17.0)])
        pred = make_invoice(items=[])
        deltas = field_deltas(gt, pred, ("items.tax_rate",))
        assert deltas[0]["changed"] is True


# --- consistency verdict ---------------------------------------------------


class TestConsistencyVerdict:
    def test_arithmetic_consistent_and_inconsistent(self):
        good = make_invoice(amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=113.0)
        bad = make_invoice(amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=120.0)
        assert consistency_verdict("arithmetic_mismatch", good, 0.02) is True
        assert consistency_verdict("arithmetic_mismatch", bad, 0.02) is False

    def test_arithmetic_undeterminable_when_a_component_is_missing(self):
        doc = InvoiceDocument(amount_including_tax=100.0)
        assert consistency_verdict("arithmetic_mismatch", doc, 0.02) is None

    def test_future_date(self):
        from datetime import date, timedelta

        assert consistency_verdict("future_date", make_invoice(issue_date="2024-01-01")) is True
        future = (date.today() + timedelta(days=400)).isoformat()
        assert consistency_verdict("future_date", make_invoice(issue_date=future)) is False
        assert consistency_verdict("future_date", InvoiceDocument()) is None

    def test_self_dealing(self):
        same = make_invoice(buyer_name="A公司", seller_name="A公司")
        diff = make_invoice(buyer_name="A公司", seller_name="B公司")
        assert consistency_verdict("self_dealing", same) is False
        assert consistency_verdict("self_dealing", diff) is True

    def test_duplicate_number_is_batch_scoped_hence_undeterminable(self):
        assert consistency_verdict("duplicate_number", make_invoice()) is None

    def test_unknown_class_is_undeterminable(self):
        assert consistency_verdict("not_a_class", make_invoice()) is None


# --- divergence classification --------------------------------------------


class TestClassifyDivergences:
    def _gt_files(self):
        return {
            "ok.pdf": {"invoice_number": "24417000000000000001", "anomalies": []},
            "bad.pdf": {"invoice_number": "24417000000000000002", "anomalies": ["arithmetic_mismatch"]},
        }

    def test_masked_anomaly_is_explained_by_a_changed_driving_field(self):
        gt_doc = make_invoice(
            number="24417000000000000002",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=120.0,
        )
        # OCR returned a self-consistent but wrong triple -> the rule cannot fire
        pred_doc = make_invoice(
            number="24417000000000000002",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=113.0,
        )
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {"fn": ["bad.pdf"]}}),
            names=["bad.pdf"],
            gt_docs=[gt_doc],
            pred_docs=[pred_doc],
            findings=[],
        )
        assert len(result["masked"]) == 1
        item = result["masked"][0]
        assert item["invoice"] == "bad.pdf"
        assert item["class"] == "arithmetic_mismatch"
        assert item["gt_detected"] is True
        assert item["changed_fields"] == ["amount_including_tax"]
        assert item["internally_consistent"] is True
        assert item["explained_by_extraction"] is True

    def test_manufactured_anomaly_on_an_unlabelled_invoice(self):
        gt_doc = make_invoice(
            number="24417000000000000001",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=113.0,
        )
        pred_doc = make_invoice(
            number="24417000000000000001",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=130.0,
        )
        findings = [
            SimpleNamespace(
                rule_id="arithmetic_total", field="amount_including_tax",
                severity=SimpleNamespace(name="ERROR"), message="mismatch", invoice_index=0,
            )
        ]
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"], "fp": ["ok.pdf"]}}),
            names=["ok.pdf"],
            gt_docs=[gt_doc],
            pred_docs=[pred_doc],
            findings=findings,
        )
        assert len(result["manufactured"]) == 1
        item = result["manufactured"][0]
        assert item["invoice"] == "ok.pdf"
        assert item["explained_by_extraction"] is True
        assert item["changed_fields"] == ["amount_including_tax"]
        assert item["e2e_findings"][0]["rule_id"] == "arithmetic_total"

    def test_masked_without_a_field_change_is_flagged_as_unexplained(self):
        gt_doc = make_invoice(
            number="24417000000000000002",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=120.0,
        )
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {"fn": ["bad.pdf"]}}),
            names=["bad.pdf"],
            gt_docs=[gt_doc],
            pred_docs=[gt_doc.model_copy(deep=True)],
            findings=[],
        )
        item = result["masked"][0]
        assert item["changed_fields"] == []
        assert item["explained_by_extraction"] is False
        assert item["unexplained"] is True

    def test_labels_on_unextractable_invoices_are_reported_separately(self):
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {}}),
            names=[],
            gt_docs=[],
            pred_docs=[],
            findings=[],
            extraction_failures=["bad.pdf"],
        )
        assert result["masked"] == []
        assert len(result["unauditable"]) == 1
        assert result["unauditable"][0]["invoice"] == "bad.pdf"
        assert result["unauditable"][0]["classes"] == ["arithmetic_mismatch"]

    def test_agreement_produces_no_divergences(self):
        doc = make_invoice(number="24417000000000000002")
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            names=["bad.pdf"],
            gt_docs=[doc],
            pred_docs=[doc.model_copy(deep=True)],
            findings=[],
        )
        assert result["masked"] == []
        assert result["manufactured"] == []
        assert result["unauditable"] == []

    def test_masked_value_error_carries_a_mechanism_label(self):
        gt_doc = make_invoice(
            number="24417000000000000002",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=120.0,
        )
        pred_doc = make_invoice(
            number="24417000000000000002",
            amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=113.0,
        )
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {"fn": ["bad.pdf"]}}),
            names=["bad.pdf"],
            gt_docs=[gt_doc],
            pred_docs=[pred_doc],
            findings=[],
        )
        assert result["masked"][0]["mechanism"] == "masked_value_error"

    def test_manufactured_carries_a_mechanism_label(self):
        gt_doc = make_invoice(number="24417000000000000001",
                              amount_excluding_tax=100.0, tax_amount=13.0,
                              amount_including_tax=113.0)
        pred_doc = make_invoice(number="24417000000000000001",
                                amount_excluding_tax=100.0, tax_amount=13.0,
                                amount_including_tax=130.0)
        result = classify_divergences(
            self._gt_files(),
            gt_audit=_audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}}),
            e2e_audit=_audit({"arithmetic_mismatch": {"fp": ["ok.pdf"]}}),
            names=["ok.pdf"],
            gt_docs=[gt_doc],
            pred_docs=[pred_doc],
            findings=[],
        )
        assert result["manufactured"][0]["mechanism"] == "manufactured_value_error"

    def test_batch_scoped_masking_is_explained_by_a_lost_partner(self):
        """`dup_invoice_number` is batch-scoped.

        If one of the two invoices sharing a number fails extraction it is
        dropped from the batch, the surviving invoice's number is then unique,
        and the rule cannot fire even though its own fields were read
        perfectly. Reporting that as "unexplained" would be wrong.
        """
        gt_files = {
            "a.pdf": {"invoice_number": "DUP", "anomalies": ["duplicate_number"]},
            "b.pdf": {"invoice_number": "DUP", "anomalies": ["duplicate_number"]},
        }
        doc = make_invoice(number="DUP")
        result = classify_divergences(
            gt_files,
            gt_audit=_audit({"duplicate_number": {"tp": ["a.pdf", "b.pdf"]}}),
            e2e_audit=_audit({"duplicate_number": {"fn": ["a.pdf"]}}),
            names=["a.pdf"],
            gt_docs=[doc],
            pred_docs=[doc.model_copy(deep=True)],
            findings=[],
            extraction_failures=["b.pdf"],
        )
        item = result["masked"][0]
        assert item["invoice"] == "a.pdf"
        assert item["mechanism"] == "masked_partner_lost"
        assert item["explained_by_extraction"] is True
        assert item["unexplained"] is False
        assert [p["invoice"] for p in item["partner_state"]] == ["b.pdf"]
        assert item["partner_state"][0]["audited"] is False

    def test_batch_scoped_masking_partner_present_and_still_matching(self):
        """Both partners audited with the same number: the rule *should* fire.

        That is a genuine harness problem, so it stays unexplained.
        """
        gt_files = {
            "a.pdf": {"invoice_number": "DUP", "anomalies": ["duplicate_number"]},
            "b.pdf": {"invoice_number": "DUP", "anomalies": ["duplicate_number"]},
        }
        docs = [make_invoice(number="DUP"), make_invoice(number="DUP")]
        result = classify_divergences(
            gt_files,
            gt_audit=_audit({"duplicate_number": {"tp": ["a.pdf", "b.pdf"]}}),
            e2e_audit=_audit({"duplicate_number": {"fn": ["a.pdf", "b.pdf"]}}),
            names=["a.pdf", "b.pdf"],
            gt_docs=docs,
            pred_docs=[d.model_copy(deep=True) for d in docs],
            findings=[],
        )
        item = result["masked"][0]
        assert item["mechanism"] == "masked_unexplained"
        assert item["unexplained"] is True
        assert item["partner_state"][0]["audited"] is True
        assert item["partner_state"][0]["same_extracted_number"] is True


# --- markdown renderer -----------------------------------------------------


class TestRenderE2EMarkdown:
    def _report(self) -> dict:
        gt_audit = _audit({"arithmetic_mismatch": {"tp": ["bad.pdf"]}})
        e2e_audit = _audit({"arithmetic_mismatch": {"fn": ["bad.pdf"]}})
        full = _audit({"arithmetic_mismatch": {"fn": ["bad.pdf"]}})
        round1 = {
            "round": 1,
            "extracted_names": ["ok.pdf"],
            "extraction_failures": [{"file": "bad.pdf", "error": "malformed JSON"}],
            "field_report": _field_report({"invoice_number": 0.5}, 0.5),
            "per_invoice": [
                {
                    "file": "ok.pdf",
                    "fields": {
                        "invoice_number": {"match": False, "compared": True, "gt": "1", "pred": "2"}
                    },
                }
            ],
            "audit_gt": gt_audit,
            "audit_e2e": e2e_audit,
            "audit_e2e_full": full,
            "elapsed_s": 100.0,
            "divergences": {
                "masked": [
                    {
                        "class": "arithmetic_mismatch",
                        "invoice": "bad.pdf",
                        "gt_detected": True,
                        "changed_fields": ["amount_including_tax"],
                        "internally_consistent": True,
                        "explained_by_extraction": True,
                        "unexplained": False,
                        "deltas": [
                            {
                                "field": "amount_including_tax",
                                "gt": 120.0,
                                "pred": 113.0,
                                "changed": True,
                            }
                        ],
                        "e2e_findings": [],
                    }
                ],
                "manufactured": [],
                "unauditable": [],
            },
        }
        return {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00",
            "extractor": "dashscope",
            "models": {"primary": "qwen-vl-plus", "fallback": "qwen3.5-ocr"},
            "n_invoices": 30,
            "rounds_requested": 2,
            "rounds_completed": 2,
            "aborted": None,
            "tolerance": 0.02,
            "pricing": {p.model: p.__dict__ for p in PRICING.values()},
            "rounds": [round1],
            "summary": summarize_rounds([round1]),
            "comparison": {
                "labelled": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 12, "fp": 0, "fn": 0},
                "extracted_subset": {"precision": 1.0, "recall": 0.9, "f1": 0.95, "tp": 9, "fp": 0, "fn": 1},
                "full_batch": {"precision": 1.0, "recall": 0.85, "f1": 0.92, "tp": 9, "fp": 0, "fn": 2},
            },
            "cost": {
                "total_cost_cny": 0.31,
                "total_prompt_tokens": 52000,
                "total_completion_tokens": 18000,
                "n_calls": 63,
                "n_failed_calls": 4,
                "budget_cny": 25.0,
                "by_model": {"qwen-vl-plus": {"calls": 63, "cost_cny": 0.31}},
            },
            "latency": {
                "total_seconds": 300.0,
                "mean_invoice_seconds": 8.5,
                "per_round_seconds": [150.0],
                "invoices_per_minute": 6.0,
            },
            "extrapolation": {
                "invoices": 100000,
                "cost_cny": 1033.0,
                "wall_clock_hours": 277.8,
                "workers": 4,
            },
        }

    def test_renders_the_required_sections(self):
        md = render_e2e_markdown(self._report())
        for heading in (
            "# End-to-end evaluation",
            "## Evaluation set",
            "## Cost and latency",
            "## Per-round raw results",
            "## End-to-end vs labelled fields",
            "## Failure modes",
            "## Recommendations",
            "## Honest limitations",
        ):
            assert heading in md, heading

    def test_renders_measured_numbers(self):
        md = render_e2e_markdown(self._report())
        assert "0.31" in md  # measured cost
        assert "1033" in md or "1,033" in md  # 100k extrapolation
        assert "qwen-vl-plus" in md

    def test_renders_a_concrete_masked_instance(self):
        md = render_e2e_markdown(self._report())
        assert "bad.pdf" in md
        assert "120.0" in md and "113.0" in md

    def test_reports_both_field_accuracy_conventions_and_the_baseline(self):
        """The recorded 0.8249 baseline counts a failed extraction as an empty
        document. Comparing a subset-only accuracy against it would flatter the
        pipeline, so both conventions are published."""
        report = self._report()
        report["rounds"][0]["field_report_allin"] = _field_report(
            {"invoice_number": 0.8}, 0.8
        )
        md = render_e2e_markdown(report)
        assert "0.8249" in md
        assert "failures counted as empty documents" in md
        assert "0.8000" in md  # the all-in figure
        assert "0.5000" in md  # the auditable-subset figure, side by side

    def test_missing_all_in_accuracy_is_stated_not_invented(self):
        md = render_e2e_markdown(self._report())
        assert "not measured" in md.lower()

    def test_does_not_publish_an_unmeasured_per_call_parse_failure_count(self):
        """The usage hook fires before JSON parsing, so the meter cannot know
        whether a given call's response was usable. Printing its `ok` counter as
        a parse-failure count would be a fabricated measurement."""
        md = render_e2e_markdown(self._report())
        assert "returned tokens but failed to parse" not in md
        assert "not separately metered" in md
        assert "Extraction failures" in md  # the invoice-level count IS measured

    def test_limitations_disclose_a_non_working_fallback_model(self):
        """The extraction-failure rate is only interpretable if the reader knows
        whether the fallback model was available to absorb primary failures."""
        md = render_e2e_markdown(self._report())
        limitations = md.split("## Honest limitations", 1)[1].lower()
        assert "fallback" in limitations
        assert "quota" in limitations or "403" in limitations

    def test_surfaces_unattributed_findings(self):
        """A finding that cannot be resolved to an invoice is never credited.

        Hiding that count would make the precision figure look better than the
        evidence supports, so the page has to state it.
        """
        report = self._report()
        report["rounds"][0]["audit_e2e"]["unattributed_findings"] = [
            {
                "rule_id": "dup_invoice_number",
                "invoice_index": None,
                "invoice_number": "99999999999999999999",
            }
        ]
        md = render_e2e_markdown(report)
        assert "unattributed" in md.lower()
        assert "dup_invoice_number" in md
        assert "99999999999999999999" in md

    def test_flags_unmeasurable_when_no_round_succeeded(self):
        report = self._report()
        report["rounds"] = []
        report["rounds_completed"] = 0
        report["aborted"] = {"reason": "budget exceeded"}
        md = render_e2e_markdown(report)
        assert "budget exceeded" in md
        assert "not measured" in md.lower() or "unmeasurable" in md.lower()


# --- endpoint redaction ----------------------------------------------------


class TestRedactEndpoint:
    """The JSON artifact is committed to a public repo: a dedicated MaaS
    endpoint carries an account-specific subdomain, so only its suffix ships."""

    def test_public_dashscope_host_is_kept(self):
        assert (
            redact_endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1")
            == "dashscope.aliyuncs.com"
        )

    def test_dedicated_deployment_host_is_redacted(self):
        redacted = redact_endpoint(
            "https://llm-jdoljb6b61vvjvrf.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        assert "llm-jdoljb6b61vvjvrf" not in redacted
        assert "maas.aliyuncs.com" in redacted
        assert "redacted" in redacted

    def test_garbage_does_not_crash(self):
        assert redact_endpoint("") == ""


# --- extractor usage hook --------------------------------------------------


class _FakeCompletions:
    def __init__(self, content: str, usage):
        self.content = content
        self.usage = usage
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=self.usage,
        )


class _FakeClient:
    def __init__(self, content: str = '{"发票号码": "24417000000000000001"}', usage=None):
        self.chat = SimpleNamespace(completions=_FakeCompletions(content, usage))


class TestDashScopeUsageRecorder:
    def _extractor(self, client, recorder=None):
        from app.core.config import Settings

        ex = DashScopeExtractor(
            settings=Settings(dashscope_api_key="test-key"), usage_recorder=recorder
        )
        ex._client = client
        return ex

    def test_recorder_receives_tokens_for_each_model_call(self):
        seen: list[tuple] = []
        usage = SimpleNamespace(prompt_tokens=1697, completion_tokens=445, total_tokens=2142)
        client = _FakeClient(usage=usage)
        ex = self._extractor(client, lambda model, u: seen.append((model, u)))

        ex._call_model("qwen-vl-plus", b"png-bytes", "image/png")

        assert seen == [
            ("qwen-vl-plus", {"prompt_tokens": 1697, "completion_tokens": 445, "total_tokens": 2142})
        ]

    def test_usage_is_recorded_even_when_the_response_is_unparseable(self):
        """A call that returns billed tokens and then fails to parse still costs."""
        seen: list[tuple] = []
        usage = SimpleNamespace(prompt_tokens=1697, completion_tokens=1903, total_tokens=3600)
        client = _FakeClient(content="not json at all", usage=usage)
        ex = self._extractor(client, lambda model, u: seen.append((model, u)))

        with pytest.raises(ValueError):
            ex._call_model("qwen-vl-plus", b"png-bytes", "image/png")
        assert seen and seen[0][1]["completion_tokens"] == 1903

    def test_missing_usage_does_not_crash(self):
        seen: list[tuple] = []
        client = _FakeClient(usage=None)
        ex = self._extractor(client, lambda model, u: seen.append((model, u)))
        ex._call_model("qwen-vl-plus", b"png-bytes", "image/png")
        assert seen == [("qwen-vl-plus", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})]

    def test_no_recorder_is_a_no_op(self):
        client = _FakeClient(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
        ex = self._extractor(client, None)
        assert ex._call_model("qwen-vl-plus", b"png-bytes", "image/png")

    def test_request_shape_is_unchanged_by_the_hook(self):
        """Recording usage must not alter the prompt or the sampling parameters."""
        client = _FakeClient(usage=None)
        ex = self._extractor(client, lambda *_: None)
        ex._call_model("qwen-vl-plus", b"png-bytes", "image/png")

        call = client.chat.completions.calls[0]
        assert call["model"] == "qwen-vl-plus"
        assert call["temperature"] == 0.0
        assert call["response_format"] == {"type": "json_object"}
        content = call["messages"][0]["content"]
        assert content[0]["text"] == _EXTRACTION_PROMPT
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
