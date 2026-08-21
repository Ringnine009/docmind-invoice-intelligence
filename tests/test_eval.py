"""Benchmark/eval harness tests."""

from app.services.eval.metrics import compare_documents, field_accuracy_report

from conftest import make_invoice


class TestCompareDocuments:
    def test_exact_string_match(self):
        gt = make_invoice(number="11111111111111111111")
        pred = make_invoice(number="11111111111111111111")
        result = compare_documents(gt, pred)
        assert result["invoice_number"]["match"] is True
        assert result["invoice_number"]["confidence"] == 1.0

    def test_string_mismatch(self):
        gt = make_invoice(number="11111111111111111111")
        pred = make_invoice(number="22222222222222222222")
        result = compare_documents(gt, pred)
        assert result["invoice_number"]["match"] is False

    def test_numeric_tolerance(self):
        gt = make_invoice(amount_including_tax=199.00)
        pred = make_invoice(amount_including_tax=199.01)
        result = compare_documents(gt, pred)
        assert result["amount_including_tax"]["match"] is True

    def test_numeric_mismatch_beyond_tolerance(self):
        gt = make_invoice(amount_including_tax=199.00)
        pred = make_invoice(amount_including_tax=250.00)
        result = compare_documents(gt, pred)
        assert result["amount_including_tax"]["match"] is False

    def test_missing_pred_field_fails(self):
        gt = make_invoice(number="11111111111111111111")
        pred = make_invoice(number=None)
        result = compare_documents(gt, pred)
        assert result["invoice_number"]["match"] is False

    def test_date_comparison(self):
        gt = make_invoice(issue_date="2024-07-20")
        pred = make_invoice(issue_date="2024-07-21")
        result = compare_documents(gt, pred)
        assert result["issue_date"]["match"] is False

    def test_whitespace_insensitive(self):
        gt = make_invoice(seller_name="示例贸易有限公司")
        pred = make_invoice(seller_name=" 示例贸易有限公司 ")
        result = compare_documents(gt, pred)
        assert result["seller.name"]["match"] is True


class TestFieldAccuracyReport:
    def test_report_aggregates(self):
        gt = [make_invoice(number="11111111111111111111"), make_invoice(number="22222222222222222222")]
        pred = [make_invoice(number="11111111111111111111"), make_invoice(number="99999999999999999999")]
        report = field_accuracy_report(gt, pred)
        assert report["invoice_number"]["accuracy"] == 0.5
        assert report["overall"]["accuracy"] == report["overall"]["accuracy"]

    def test_report_all_correct(self):
        gt = [make_invoice(number="11111111111111111111")] * 2
        pred = [make_invoice(number="11111111111111111111")] * 2
        report = field_accuracy_report(gt, pred)
        assert report["invoice_number"]["accuracy"] == 1.0

    def test_report_empty(self):
        report = field_accuracy_report([], [])
        assert report["overall"]["accuracy"] == 1.0  # vacuous truth
