"""Audit-engine rule tests (red first: these defined the behaviour)."""

from datetime import date

import pytest

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule, get_registered_rules
from app.services.audit.engine import AuditEngine

from conftest import make_invoice, make_item


def rule_ids(batch: list[InvoiceDocument]) -> set[str]:
    engine = AuditEngine()
    return {f.rule_id for f in engine.run(batch)}


def findings_for(batch: list[InvoiceDocument], rule_id: str) -> list[AuditFinding]:
    engine = AuditEngine()
    return [f for f in engine.run(batch) if f.rule_id == rule_id]


class TestRegistry:
    def test_all_rules_registered(self):
        ids = get_registered_rules()
        assert "dup_invoice_number" in ids
        assert "arithmetic_total" in ids
        assert "line_items_sum" in ids
        assert "tax_rate" in ids
        assert "party_info" in ids
        assert "invoice_date" in ids
        assert "low_confidence" in ids
        assert "qr_crosscheck" in ids

    def test_registered_rules_are_audit_rule_instances(self):
        for rule in get_registered_rules().values():
            assert isinstance(rule, AuditRule)
            assert rule.rule_id
            assert rule.name
            assert rule.description


class TestDuplicateInvoiceNumberRule:
    def test_duplicate_detected(self):
        batch = [
            make_invoice(number="11111111111111111111", amount_including_tax=100),
            make_invoice(number="11111111111111111111", amount_including_tax=200),
            make_invoice(number="22222222222222222222", amount_including_tax=300),
        ]
        findings = findings_for(batch, "dup_invoice_number")
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].evidence["number"] == "11111111111111111111"
        assert findings[0].evidence["count"] == 2
        assert findings[0].evidence["invoice_indices"] == [0, 1]

    def test_no_duplicates(self):
        batch = [make_invoice(number=f"{i:020d}") for i in range(3)]
        assert findings_for(batch, "dup_invoice_number") == []

    def test_missing_numbers_ignored(self):
        batch = [make_invoice(number=None), make_invoice(number=None)]
        assert findings_for(batch, "dup_invoice_number") == []

    def test_triplicate_detected(self):
        batch = [make_invoice(number="99999999999999999999") for _ in range(3)]
        findings = findings_for(batch, "dup_invoice_number")
        assert len(findings) == 1
        assert findings[0].evidence["count"] == 3


class TestArithmeticTotalRule:
    def test_consistent_totals_pass(self):
        batch = [make_invoice(amount_excluding_tax=176.11, tax_amount=22.89, amount_including_tax=199.00)]
        assert findings_for(batch, "arithmetic_total") == []

    def test_mismatch_detected(self):
        batch = [make_invoice(amount_excluding_tax=176.11, tax_amount=22.89, amount_including_tax=250.00)]
        findings = findings_for(batch, "arithmetic_total")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert findings[0].invoice_number == "24417000000034170288"

    def test_tiny_rounding_diff_ok(self):
        batch = [make_invoice(amount_excluding_tax=111.33, tax_amount=14.47, amount_including_tax=125.81)]
        assert findings_for(batch, "arithmetic_total") == []

    def test_missing_total_warns(self):
        batch = [make_invoice(amount_excluding_tax=100.0, tax_amount=13.0, amount_including_tax=None)]
        findings = findings_for(batch, "arithmetic_total")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_missing_component_warns(self):
        batch = [make_invoice(amount_excluding_tax=100.0, tax_amount=None, amount_including_tax=113.0)]
        findings = findings_for(batch, "arithmetic_total")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_all_missing_skipped(self):
        batch = [make_invoice(amount_excluding_tax=None, tax_amount=None, amount_including_tax=None)]
        assert findings_for(batch, "arithmetic_total") == []


class TestLineItemsSumRule:
    def test_line_sum_matches_total(self):
        items = [
            make_item(name="A", amount=100.0, tax_amount=13.0, tax_rate=13.0),
            make_item(name="B", amount=76.11, tax_amount=9.89, tax_rate=13.0),
        ]
        batch = [make_invoice(items=items, amount_excluding_tax=176.11, tax_amount=22.89)]
        assert findings_for(batch, "line_items_sum") == []

    def test_line_sum_mismatch_warns(self):
        items = [make_item(name="A", amount=100.0, tax_amount=13.0)]
        batch = [make_invoice(items=items, amount_excluding_tax=150.0, tax_amount=13.0)]
        findings = findings_for(batch, "line_items_sum")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_no_items_skipped(self):
        batch = [make_invoice(items=[], amount_excluding_tax=100.0)]
        assert findings_for(batch, "line_items_sum") == []


class TestTaxRateRule:
    def test_common_rates_pass(self):
        for rate in (0.0, 1.0, 3.0, 6.0, 9.0, 13.0):
            item = make_item(amount=100.0, tax_amount=round(100.0 * rate / 100, 2), tax_rate=rate)
            batch = [make_invoice(items=[item], tax_amount=round(100.0 * rate / 100, 2))]
            assert findings_for(batch, "tax_rate") == []

    def test_anomalous_rate_detected(self):
        batch = [make_invoice(items=[make_item(tax_rate=17.0)])]
        findings = findings_for(batch, "tax_rate")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert findings[0].evidence["rate"] == 17.0

    def test_missing_rate_warns(self):
        batch = [make_invoice(items=[make_item(tax_rate=None)])]
        findings = findings_for(batch, "tax_rate")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_implied_rate_mismatch_detected(self):
        # declared 6% but amounts imply 13%
        batch = [make_invoice(items=[make_item(amount=100.0, tax_amount=13.0, tax_rate=6.0)])]
        findings = findings_for(batch, "tax_rate")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "implied" in findings[0].evidence

    def test_implied_rate_matches(self):
        batch = [make_invoice(items=[make_item(amount=100.0, tax_amount=13.0, tax_rate=13.0)])]
        assert findings_for(batch, "tax_rate") == []


class TestPartyInfoRule:
    def test_missing_seller_name_warns(self):
        batch = [make_invoice(seller_name="")]
        findings = findings_for(batch, "party_info")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_missing_buyer_tax_id_warns(self):
        batch = [make_invoice(buyer_tax_id=None)]
        findings = findings_for(batch, "party_info")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_invalid_tax_id_detected(self):
        batch = [make_invoice(buyer_tax_id="abc!@#")]
        findings = findings_for(batch, "party_info")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR

    def test_failed_checksum_tax_id_warns(self):
        # valid 18-char format, wrong check character → checksum warning
        batch = [make_invoice(buyer_tax_id="12100000425006125K")]
        findings = findings_for(batch, "party_info")
        assert any(f.severity == Severity.WARNING and "checksum" in f.message for f in findings)

    def test_valid_uscc_passes_checksum(self):
        batch = [make_invoice(buyer_tax_id="12100000425006125J",
                              seller_tax_id="91310107MA1G1C8Q5W")]
        findings = findings_for(batch, "party_info")
        assert all("checksum" not in (f.message or "") for f in findings)

    def test_buyer_equals_seller_detected(self):
        batch = [make_invoice(buyer_name="同一公司", seller_name="同一公司")]
        findings = findings_for(batch, "party_info")
        assert any(f.severity == Severity.ERROR for f in findings)
        assert any("self" in (f.message or "").lower() for f in findings)

    def test_same_tax_id_detected(self):
        batch = [make_invoice(buyer_tax_id="91440183797370649Q", seller_tax_id="91440183797370649Q")]
        findings = findings_for(batch, "party_info")
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_clean_parties_pass(self):
        batch = [make_invoice()]
        assert findings_for(batch, "party_info") == []


class TestInvoiceDateRule:
    def test_valid_date_passes(self):
        batch = [make_invoice(issue_date="2024-07-20")]
        assert findings_for(batch, "invoice_date") == []

    def test_missing_date_warns(self):
        batch = [make_invoice(issue_date=None)]
        findings = findings_for(batch, "invoice_date")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_future_date_warns(self):
        batch = [make_invoice(issue_date="2099-01-01")]
        findings = findings_for(batch, "invoice_date")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING


class TestLowConfidenceRule:
    def test_low_confidence_field_warns(self):
        batch = [make_invoice(confidence={"invoice_number": 0.42, "amount_including_tax": 0.99})]
        findings = findings_for(batch, "low_confidence")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "invoice_number" in findings[0].evidence["fields"]

    def test_all_high_confidence_passes(self):
        batch = [make_invoice(confidence={"invoice_number": 0.95})]
        assert findings_for(batch, "low_confidence") == []


class TestQrCrosscheckRule:
    """QR payload (number/amount/date) vs. extracted fields."""

    PAYLOAD = "01,32,,24417000000034170288,199.00,20240720"

    def test_consistent_payload_passes(self):
        batch = [make_invoice(number="24417000000034170288",
                              amount_including_tax=199.00,
                              issue_date="2024-07-20",
                              qr_payload=self.PAYLOAD)]
        assert findings_for(batch, "qr_crosscheck") == []

    def test_number_mismatch_detected(self):
        batch = [make_invoice(number="99999999999999999999",
                              qr_payload=self.PAYLOAD)]
        findings = findings_for(batch, "qr_crosscheck")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert findings[0].evidence["qr_number"] == "24417000000034170288"
        assert findings[0].evidence["extracted_number"] == "99999999999999999999"

    def test_amount_mismatch_detected(self):
        batch = [make_invoice(number="24417000000034170288",
                              amount_including_tax=299.00,
                              qr_payload=self.PAYLOAD)]
        findings = findings_for(batch, "qr_crosscheck")
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert findings[0].evidence["qr_amount"] == 199.00

    def test_amount_within_tolerance_passes(self):
        batch = [make_invoice(number="24417000000034170288",
                              amount_including_tax=199.01,
                              qr_payload=self.PAYLOAD)]
        assert findings_for(batch, "qr_crosscheck") == []

    def test_date_mismatch_warns(self):
        batch = [make_invoice(number="24417000000034170288",
                              amount_including_tax=199.00,
                              issue_date="2024-08-01",
                              qr_payload=self.PAYLOAD)]
        findings = findings_for(batch, "qr_crosscheck")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_missing_payload_skipped(self):
        batch = [make_invoice(number="24417000000034170288", qr_payload=None)]
        assert findings_for(batch, "qr_crosscheck") == []

    def test_garbage_payload_warns(self):
        batch = [make_invoice(number="24417000000034170288",
                              qr_payload="not-a-valid-payload")]
        findings = findings_for(batch, "qr_crosscheck")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_unparseable_date_only_warns_on_date(self):
        batch = [make_invoice(number="24417000000034170288",
                              amount_including_tax=199.00,
                              issue_date="2024-07-20",
                              qr_payload="01,32,,24417000000034170288,199.00,99999999")]
        findings = findings_for(batch, "qr_crosscheck")
        # number & amount still match; only the date field is suspect
        assert all(f.severity == Severity.WARNING for f in findings)
        assert all("date" in (f.field or "") for f in findings)


class TestAuditEngine:
    def test_engine_runs_all_rules(self):
        batch = [
            make_invoice(number="11111111111111111111"),
            make_invoice(number="11111111111111111111", amount_including_tax=999.0),
        ]
        findings = AuditEngine().run(batch)
        assert isinstance(findings, list)
        assert all(isinstance(f, AuditFinding) for f in findings)

    def test_findings_sorted_by_severity_desc(self):
        batch = [
            make_invoice(number="11111111111111111111"),
            make_invoice(number="11111111111111111111", amount_including_tax=999.0),
        ]
        findings = AuditEngine().run(batch)
        severities = [f.severity for f in findings]
        assert severities == sorted(severities, key=lambda s: s.value, reverse=True)

    def test_disabled_rule_skipped(self):
        batch = [make_invoice(amount_excluding_tax=1.0, tax_amount=1.0, amount_including_tax=99.0)]
        engine = AuditEngine(disabled_rules={"arithmetic_total"})
        assert all(f.rule_id != "arithmetic_total" for f in engine.run(batch))

    def test_empty_batch(self):
        assert AuditEngine().run([]) == []

    def test_invoice_reference_is_set(self):
        batch = [make_invoice(number="11111111111111111111", amount_including_tax=999.0)]
        findings = findings_for(batch, "arithmetic_total")
        assert findings[0].invoice_index == 0
        assert findings[0].invoice_number == "11111111111111111111"
