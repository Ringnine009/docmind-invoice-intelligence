"""Core data models: extraction schema and audit models."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

ConfidenceValue = Annotated[float, Field(ge=0.0, le=1.0)]


class InvoiceItem(BaseModel):
    """One line item on an invoice (项目明细行)."""

    name: str = Field(min_length=1)
    specification: str | None = None
    unit: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount_excluding_tax: float | None = None
    tax_rate: float | None = None  # percent, e.g. 13.0
    tax_amount: float | None = None


class InvoiceParty(BaseModel):
    """A buyer or seller party on the invoice."""

    name: str = ""
    tax_id: str | None = None


class InvoiceDocument(BaseModel):
    """Normalized extraction result for one invoice.

    ``confidence`` maps field names (English, dotted for nested) to a value in
    [0, 1] produced by the vision model (or 1.0 when unavailable).
    ``qr_payload`` is the raw string decoded from the invoice QR code (used by
    the ``qr_crosscheck`` audit rule). ``corrections`` records deterministic
    post-extraction repairs (e.g. tax-id check-character fixes).
    """

    invoice_type: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    buyer: InvoiceParty = Field(default_factory=InvoiceParty)
    seller: InvoiceParty = Field(default_factory=InvoiceParty)
    items: list[InvoiceItem] = Field(default_factory=list)
    amount_excluding_tax: float | None = None
    tax_amount: float | None = None
    amount_including_tax: float | None = None
    amount_in_words: str | None = None
    remarks: str | None = None
    issuer: str | None = None
    check_code: str | None = None
    qr_payload: str | None = None
    corrections: dict[str, str] = Field(default_factory=dict)
    confidence: dict[str, ConfidenceValue] = Field(default_factory=dict)
