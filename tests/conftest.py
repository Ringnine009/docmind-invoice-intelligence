"""Shared fixtures and builders for the DocMind test-suite."""

from __future__ import annotations

from app.models.invoice import InvoiceDocument, InvoiceItem, InvoiceParty


def make_item(
    name: str = "计算机外部设备*固态硬盘",
    quantity: float | None = 1,
    unit_price: float | None = 884.07,
    amount: float | None = 884.07,
    tax_rate: float | None = 13.0,
    tax_amount: float | None = 114.93,
) -> InvoiceItem:
    return InvoiceItem(
        name=name,
        specification=None,
        unit="个" if quantity else None,
        quantity=quantity,
        unit_price=unit_price,
        amount_excluding_tax=amount,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
    )


def make_invoice(
    number: str = "24417000000034170288",
    issue_date: str = "2024-07-20",
    buyer_name: str = "同济大学",
    buyer_tax_id: str = "12100000425006125J",
    seller_name: str = "示例贸易有限公司",
    seller_tax_id: str = "91440183797370649Q",
    amount_excluding_tax: float | None = 176.11,
    tax_amount: float | None = 22.89,
    amount_including_tax: float | None = 199.00,
    items: list[InvoiceItem] | None = None,
    issuer: str | None = "王梅",
    check_code: str | None = "51191401325570116214",
    confidence: dict[str, float] | None = None,
    invoice_type: str | None = "电子发票（普通发票）",
    remarks: str | None = None,
) -> InvoiceDocument:
    """Build a valid invoice document with sane defaults."""
    return InvoiceDocument(
        invoice_type=invoice_type,
        invoice_number=number,
        issue_date=issue_date,
        buyer=InvoiceParty(name=buyer_name, tax_id=buyer_tax_id),
        seller=InvoiceParty(name=seller_name, tax_id=seller_tax_id),
        items=items if items is not None else [make_item()],
        amount_excluding_tax=amount_excluding_tax,
        tax_amount=tax_amount,
        amount_including_tax=amount_including_tax,
        amount_in_words=None,
        remarks=remarks,
        issuer=issuer,
        check_code=check_code,
        confidence=confidence or {"invoice_number": 1.0, "amount_including_tax": 1.0},
    )
