"""Schema contract tests for the extraction data model."""

import pytest
from pydantic import ValidationError

from app.models.invoice import InvoiceDocument, InvoiceItem, InvoiceParty


class TestInvoiceItem:
    def test_defaults(self):
        item = InvoiceItem(name="服务费")
        assert item.quantity is None
        assert item.tax_rate is None
        assert item.amount_excluding_tax is None

    def test_full_item(self):
        item = InvoiceItem(
            name="固态硬盘",
            specification="1TB",
            unit="个",
            quantity=2,
            unit_price=500.0,
            amount_excluding_tax=1000.0,
            tax_rate=13.0,
            tax_amount=130.0,
        )
        assert item.tax_rate == 13.0

    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            InvoiceItem(name="")


class TestInvoiceDocument:
    def test_minimal_document(self):
        doc = InvoiceDocument(
            buyer=InvoiceParty(name="A"), seller=InvoiceParty(name="B")
        )
        assert doc.invoice_number is None
        assert doc.items == []
        assert doc.confidence == {}

    def test_full_document_roundtrip(self):
        doc = InvoiceDocument(
            invoice_type="电子发票（普通发票）",
            invoice_number="24417000000034170288",
            issue_date="2024-07-20",
            buyer=InvoiceParty(name="同济大学", tax_id="12100000425006125J"),
            seller=InvoiceParty(name="示例贸易有限公司", tax_id="91440183797370649Q"),
            items=[InvoiceItem(name="固态硬盘", tax_rate=13.0, tax_amount=130.0)],
            amount_excluding_tax=1000.0,
            tax_amount=130.0,
            amount_including_tax=1130.0,
            issuer="王梅",
            check_code="51191401325570116214",
            confidence={"invoice_number": 0.99, "amount_including_tax": 0.95},
        )
        data = doc.model_dump(mode="json")
        restored = InvoiceDocument.model_validate(data)
        assert restored == doc

    def test_issue_date_parses_iso(self):
        doc = InvoiceDocument(issue_date="2024-07-20")
        assert doc.issue_date.isoformat() == "2024-07-20"

    def test_issue_date_rejects_garbage(self):
        with pytest.raises(ValidationError):
            InvoiceDocument(issue_date="not-a-date")

    def test_confidence_values_must_be_floats(self):
        with pytest.raises(ValidationError):
            InvoiceDocument(confidence={"invoice_number": "high"})

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            InvoiceDocument(confidence={"invoice_number": 1.5})

    def test_confidence_negative_rejected(self):
        with pytest.raises(ValidationError):
            InvoiceDocument(confidence={"invoice_number": -0.2})
