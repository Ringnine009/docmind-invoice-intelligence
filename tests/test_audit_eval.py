"""Audit-engine evaluation: anomaly-class ↔ rule mapping and per-class metrics.

The extraction benchmark (`tests/test_eval.py`) scores *fields*; this suite
scores *audit decisions* against the anomaly labels that
`scripts/generate_synthetic_invoices.py` injects into
`benchmark/ground_truth.json`.
"""

import json

import pytest

from app.core.config import find_repo_root
from app.models.audit import AuditFinding, Severity
from app.services.audit.base import get_registered_rules
from app.services.eval.audit_metrics import (
    ANOMALY_TO_RULES,
    AuditEvalError,
    evaluate_audit,
    evaluate_ground_truth_audit,
    mapping_as_dict,
    render_audit_markdown,
)

GT_PATH = find_repo_root() / "benchmark" / "ground_truth.json"


# --- helpers ---------------------------------------------------------------


def gt_entry(number: str, anomalies: list[str] | None = None) -> dict:
    """A minimal ground-truth entry (only the fields the eval actually needs)."""
    return {"invoice_number": number, "anomalies": anomalies or []}


def finding(rule_id: str, *, index=None, number=None, field=None) -> AuditFinding:
    return AuditFinding(
        rule_id=rule_id,
        rule_name=rule_id,
        severity=Severity.ERROR,
        message=f"{rule_id} fired",
        evidence={},
        invoice_index=index,
        invoice_number=number,
        field=field,
    )


# --- mapping contract ------------------------------------------------------


class TestMappingContract:
    def test_every_ground_truth_class_is_mapped(self):
        files = json.loads(GT_PATH.read_text(encoding="utf-8"))["files"]
        present = {a for entry in files.values() for a in entry.get("anomalies", [])}
        assert present, "ground truth carries no anomaly labels"
        assert present == set(ANOMALY_TO_RULES), (
            "every anomaly class in ground_truth.json must be mapped to rule ids"
        )

    def test_mapped_rule_ids_are_registered(self):
        registered = set(get_registered_rules())
        for anomaly, refs in ANOMALY_TO_RULES.items():
            for ref in refs:
                assert ref.rule_id in registered, (
                    f"anomaly {anomaly!r} maps to unregistered rule {ref.rule_id!r}"
                )

    def test_mapping_is_serialisable(self):
        data = mapping_as_dict()
        assert data["duplicate_number"] == ["dup_invoice_number"]
        assert "party_info@seller.tax_id" in data["missing_seller_tax_id"]


class TestMappingValidationFailsLoudly:
    """A broken mapping must raise — never silently score 0."""

    def test_unknown_anomaly_class_raises(self):
        files = {"a.pdf": gt_entry("1", ["mystery_anomaly"])}
        with pytest.raises(AuditEvalError, match="mystery_anomaly"):
            evaluate_audit(files, [], mapping={"duplicate_number": ("dup_invoice_number",)})

    def test_unregistered_rule_id_raises(self):
        files = {"a.pdf": gt_entry("1", ["duplicate_number"])}
        with pytest.raises(AuditEvalError, match="no_such_rule"):
            evaluate_audit(
                files,
                [],
                mapping={"duplicate_number": ("no_such_rule",)},
                registered_rule_ids={"dup_invoice_number"},
            )

    def test_typo_in_rule_id_raises(self):
        files = {"a.pdf": gt_entry("1", ["future_date"])}
        with pytest.raises(AuditEvalError, match="invoice_dates"):
            evaluate_audit(files, [], mapping={"future_date": ("invoice_dates",)})

    def test_empty_ground_truth_raises(self):
        with pytest.raises(AuditEvalError, match="empty"):
            evaluate_audit({}, [])

    def test_ground_truth_without_anomalies_raises(self):
        files = {"a.pdf": gt_entry("1"), "b.pdf": gt_entry("2")}
        with pytest.raises(AuditEvalError, match="no anomal"):
            evaluate_audit(files, [])


# --- per-class metrics -----------------------------------------------------


class TestPerClassMetrics:
    FILES = {
        "dup_a.pdf": gt_entry("DUP", ["duplicate_number"]),
        "dup_b.pdf": gt_entry("DUP", ["duplicate_number"]),
        "date.pdf": gt_entry("DATE", ["future_date"]),
        "clean.pdf": gt_entry("CLEAN"),
    }
    # Batch order the findings' invoice_index values refer to. Pinned
    # explicitly so the test never depends on `sorted(gt_files)` by accident.
    NAMES = ["dup_a.pdf", "dup_b.pdf", "date.pdf", "clean.pdf"]

    def test_perfect_scores(self):
        findings = [
            finding("dup_invoice_number", number="DUP"),
            finding("invoice_date", index=2),  # date.pdf
        ]
        report = evaluate_audit(self.FILES, findings, names=self.NAMES)
        dup = report["classes"]["duplicate_number"]
        assert (dup["tp"], dup["fp"], dup["fn"]) == (2, 0, 0)
        assert dup["precision"] == 1.0 and dup["recall"] == 1.0
        assert report["classes"]["future_date"]["recall"] == 1.0
        assert report["overall"]["precision"] == 1.0
        assert report["overall"]["recall"] == 1.0

    def test_missed_anomaly_is_a_false_negative(self):
        # only the duplicate rule fires; the future date is missed
        report = evaluate_audit(
            self.FILES, [finding("dup_invoice_number", number="DUP")], names=self.NAMES
        )
        d = report["classes"]["future_date"]
        assert (d["tp"], d["fp"], d["fn"]) == (0, 0, 1)
        assert d["recall"] == 0.0
        assert d["precision"] is None  # undefined, NOT silently 1.0
        assert report["classes"]["future_date"]["fn_invoices"] == ["date.pdf"]
        assert report["overall"]["recall"] == pytest.approx(2 / 3, abs=1e-4)

    def test_finding_on_clean_invoice_is_a_false_positive(self):
        findings = [
            finding("dup_invoice_number", number="DUP"),
            finding("invoice_date", index=3),  # clean.pdf has no anomaly
        ]
        report = evaluate_audit(self.FILES, findings, names=self.NAMES)
        d = report["classes"]["future_date"]
        assert (d["tp"], d["fp"], d["fn"]) == (0, 1, 1)
        assert d["precision"] == 0.0
        assert d["fp_invoices"] == ["clean.pdf"]

    def test_held_out_batch_reports_zero_support_not_perfect(self):
        # The rule never fires and no invoice is annotated for it: support 0,
        # precision/recall undefined (None) rather than a flattering 1.0.
        files = {"date.pdf": gt_entry("DATE", ["future_date"])}
        report = evaluate_audit(files, [])
        d = report["classes"]["future_date"]
        assert d["support"] == 1 and d["tp"] == 0
        assert d["precision"] is None and d["recall"] == 0.0

    def test_counts_are_invoice_level_not_finding_level(self):
        # two duplicate findings that both name the same two invoices must
        # not inflate tp to 4.
        files = {
            "a.pdf": gt_entry("DUP", ["duplicate_number"]),
            "b.pdf": gt_entry("DUP", ["duplicate_number"]),
        }
        findings = [
            finding("dup_invoice_number", number="DUP"),
            finding("dup_invoice_number", number="DUP"),
        ]
        report = evaluate_audit(files, findings)
        assert report["classes"]["duplicate_number"]["tp"] == 2


class TestAttributionNumbersCanBeOverridden:
    """A finding names the invoice number the *engine* saw.

    On labelled input that is the ground-truth number, but when the fields come
    from a vision model it is the extracted number. Resolving findings against
    the ground-truth numbers then fails for exactly the invoices whose number
    was misread — a harness artefact masquerading as a missed detection. The
    caller can therefore supply the numbers the batch was actually built from.
    """

    FILES = {
        "a.pdf": gt_entry("11111111111111111111", ["duplicate_number"]),
        "b.pdf": gt_entry("22222222222222222222", ["duplicate_number"]),
        "clean.pdf": gt_entry("33333333333333333333"),
    }
    NAMES = ["a.pdf", "b.pdf", "clean.pdf"]

    def test_gt_numbers_are_used_by_default(self):
        report = evaluate_audit(
            self.FILES, [finding("dup_invoice_number", number="11111111111111111111")],
            names=self.NAMES,
        )
        assert report["classes"]["duplicate_number"]["tp_invoices"] == ["a.pdf"]

    def test_extracted_numbers_resolve_a_misread_duplicate(self):
        # The model misread BOTH numbers as the same wrong value, so the rule
        # fires — correctly — on a pair that exists only in the extraction.
        report = evaluate_audit(
            self.FILES,
            [finding("dup_invoice_number", number="99999999999999999999")],
            names=self.NAMES,
            numbers={"a.pdf": "99999999999999999999", "b.pdf": "99999999999999999999",
                     "clean.pdf": "33333333333333333333"},
        )
        dup = report["classes"]["duplicate_number"]
        assert dup["tp_invoices"] == ["a.pdf", "b.pdf"]
        assert dup["tp"] == 2 and dup["fn"] == 0
        assert report["unattributed_findings"] == []

    def test_without_the_override_that_finding_is_unattributed(self):
        report = evaluate_audit(
            self.FILES,
            [finding("dup_invoice_number", number="99999999999999999999")],
            names=self.NAMES,
        )
        assert report["classes"]["duplicate_number"]["fn"] == 2
        assert len(report["unattributed_findings"]) == 1
        assert report["unattributed_findings"][0]["rule_id"] == "dup_invoice_number"


# --- shared rules and attribution -----------------------------------------


class TestSharedRuleAttribution:
    """`party_info` backs two anomaly classes; field selectors keep them apart."""

    FILES = {
        "a.pdf": gt_entry("A", ["missing_seller_tax_id"]),
        "b.pdf": gt_entry("B", ["self_dealing"]),
    }

    def test_field_selector_separates_two_classes_of_one_rule(self):
        findings = [
            finding("party_info", index=0, field="seller.tax_id"),
            finding("party_info", index=1, field="buyer.name"),
        ]
        report = evaluate_audit(self.FILES, findings)
        assert report["classes"]["missing_seller_tax_id"]["precision"] == 1.0
        assert report["classes"]["self_dealing"]["precision"] == 1.0
        # rule-level view: party_info is 2/2 correct on this batch
        assert report["rules"]["party_info"]["precision"] == 1.0
        assert report["rules"]["party_info"]["recall"] == 1.0

    def test_self_dealing_finding_does_not_count_for_missing_tax_id(self):
        findings = [finding("party_info", index=1, field="buyer.name")]
        report = evaluate_audit(self.FILES, findings)
        missing = report["classes"]["missing_seller_tax_id"]
        assert missing["tp"] == 0 and missing["fn"] == 1
        assert report["classes"]["self_dealing"]["tp"] == 1

    def test_finding_attributed_by_index_when_number_absent(self):
        files = {"a.pdf": gt_entry("A", ["future_date"]), "b.pdf": gt_entry("B")}
        report = evaluate_audit(files, [finding("invoice_date", index=0)])
        assert report["classes"]["future_date"]["tp_invoices"] == ["a.pdf"]

    def test_finding_attributed_by_number_when_index_absent(self):
        files = {"a.pdf": gt_entry("A", ["duplicate_number"]),
                 "b.pdf": gt_entry("A", ["duplicate_number"])}
        report = evaluate_audit(files, [finding("dup_invoice_number", number="A")])
        assert sorted(report["classes"]["duplicate_number"]["tp_invoices"]) == ["a.pdf", "b.pdf"]

    def test_unattributable_finding_is_reported_not_dropped(self):
        # A finding with neither index nor number cannot be credited to any
        # invoice — it must show up in the report instead of vanishing.
        files = {"a.pdf": gt_entry("A", ["future_date"]), "b.pdf": gt_entry("B")}
        report = evaluate_audit(files, [finding("dup_invoice_number")])
        assert report["unattributed_findings"] == [
            {"rule_id": "dup_invoice_number", "invoice_index": None, "invoice_number": None}
        ]
        assert report["overall"]["tp"] == 0

    def test_findings_from_unmapped_rules_are_reported_separately(self):
        files = {"a.pdf": gt_entry("A", ["future_date"]), "b.pdf": gt_entry("B")}
        report = evaluate_audit(
            files, [finding("invoice_date", index=0), finding("low_confidence", index=1)]
        )
        assert report["unmapped_rule_findings"] == {"low_confidence": 1}
        assert report["overall"]["fp"] == 0  # not silently counted as a class FP
        assert report["rules"]["invoice_date"]["precision"] == 1.0


# --- the real shipped ground truth ----------------------------------------


class TestRealGroundTruth:
    @pytest.fixture(scope="class")
    @classmethod
    def report(cls):
        return evaluate_ground_truth_audit()

    def test_scale(self, report):
        assert report["n_invoices"] == 30
        assert report["n_annotated_invoices"] == 12
        assert report["n_anomaly_labels"] == 12
        assert report["n_classes"] == 7

    def test_full_recall_and_no_false_positives(self, report):
        assert report["overall"]["recall"] == 1.0
        assert report["overall"]["precision"] == 1.0
        assert report["overall"]["tp"] == 12
        assert report["overall"]["fp"] == 0
        assert report["overall"]["fn"] == 0

    def test_every_class_is_perfect(self, report):
        for name, metrics in report["classes"].items():
            assert metrics["recall"] == 1.0, name
            assert metrics["precision"] == 1.0, name

    def test_file_order_is_recorded(self, report):
        assert report["file_order"] == sorted(report["file_order"])
        assert len(report["file_order"]) == 30

    def test_no_rule_crashed_during_the_run(self, report):
        assert report["rule_errors"] == []

    def test_markdown_renders_the_numbers(self, report):
        md = render_audit_markdown(report)
        assert "12" in md and "|" in md
        assert "duplicate_number" in md


class TestLimitationsReportTheMeasuredEndToEndResult:
    """This page used to defer end-to-end scoring to future work.

    It must now either report the measured end-to-end result or say plainly
    that it is unmeasured — silently dropping the caveat would let the 1.000
    headline read as an end-to-end number.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def markdown(cls):
        return render_audit_markdown(evaluate_ground_truth_audit())

    def test_points_at_the_end_to_end_page(self, markdown):
        assert "e2e-eval.md" in markdown

    def test_no_longer_promises_end_to_end_as_future_work(self, markdown):
        assert "the end-to-end path is not measured here" not in markdown
        assert "End-to-end numbers need a real extraction run" not in markdown

    def test_names_the_extracted_input_result_as_measured(self, markdown):
        lowered = markdown.lower()
        assert "measured" in lowered
        assert "micro recall" in lowered
