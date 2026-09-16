"""Shared fixtures and builders for the DocMind test-suite."""

from __future__ import annotations

from app.models.invoice import InvoiceDocument, InvoiceItem, InvoiceParty

# --- synthetic identity data (no real entity) -------------------------------
# These used to be real values copied from the original coursework dataset
# (a real university, its real unified social credit code and a real issuer's
# name) — see docs/data-compliance.md. They are now fabricated placeholders:
# company names come from the synthetic generator's fictional pools, and the
# tax ids are 18-character codes whose "organisation code" section is all
# zeros, so they cannot belong to any registered entity. Their
# GB 32100-2015 check characters are valid, because the `party_info` rule
# validates them and the tests rely on a clean default invoice passing.
# tests/test_pii_guard.py fails the build if a real identifier reappears.
BUYER_NAME = "远景云服务有限公司"
BUYER_TAX_ID = "91310000000000000U"
SELLER_NAME = "示例贸易有限公司"
SELLER_TAX_ID = "91440000000000000Y"
ISSUER = "顾星野"

#: Same body as :data:`BUYER_TAX_ID` with a deliberately wrong check character,
#: used to exercise the checksum warning and the deterministic repair.
BUYER_TAX_ID_BAD_CHECK = "913100000000000000"

INVOICE_NUMBER = "24417000000034170288"
CHECK_CODE = "51191401325570116214"


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
    number: str = INVOICE_NUMBER,
    issue_date: str = "2024-07-20",
    buyer_name: str = BUYER_NAME,
    buyer_tax_id: str = BUYER_TAX_ID,
    seller_name: str = SELLER_NAME,
    seller_tax_id: str = SELLER_TAX_ID,
    amount_excluding_tax: float | None = 176.11,
    tax_amount: float | None = 22.89,
    amount_including_tax: float | None = 199.00,
    items: list[InvoiceItem] | None = None,
    issuer: str | None = ISSUER,
    check_code: str | None = CHECK_CODE,
    confidence: dict[str, float] | None = None,
    invoice_type: str | None = "电子发票（普通发票）",
    remarks: str | None = None,
    qr_payload: str | None = None,
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
        qr_payload=qr_payload,
    )
