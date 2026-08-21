"""Buyer/seller party information sanity checks."""

from __future__ import annotations

import re

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule

# Unified social credit code: 18 chars alnum; legacy tax ids: 15/20 digits.
_TAX_ID_RE = re.compile(r"^[0-9A-Z]{15,20}$")
_USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def _uscc_checksum_ok(code: str) -> bool:
    """Validate the GB 32100-2015 check character of an 18-char USCC."""
    if len(code) != 18 or any(ch not in _USCC_CHARSET for ch in code):
        return False
    total = sum(
        _USCC_WEIGHTS[i] * _USCC_CHARSET.index(ch) for i, ch in enumerate(code[:17])
    )
    return _USCC_CHARSET[(31 - total % 31) % 31] == code[17]


class PartyInfoRule(AuditRule):
    rule_id = "party_info"
    name = "Party information integrity"
    description = (
        "Warns on missing buyer/seller names or tax ids, rejects malformed tax "
        "ids, and flags self-dealing (buyer == seller)."
    )
    default_severity = Severity.WARNING

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []

        for i, doc in enumerate(batch):
            number = doc.invoice_number
            buyer, seller = doc.buyer, doc.seller
            buyer_name = (buyer.name or "").strip()
            seller_name = (seller.name or "").strip()

            if not buyer_name:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: buyer name is missing.",
                        severity=Severity.WARNING,
                        evidence={},
                        invoice_index=i,
                        invoice_number=number,
                        field="buyer.name",
                    )
                )
            if not seller_name:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: seller name is missing.",
                        severity=Severity.WARNING,
                        evidence={},
                        invoice_index=i,
                        invoice_number=number,
                        field="seller.name",
                    )
                )

            if buyer_name and seller_name and buyer_name == seller_name:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: buyer and seller are the same "
                        f"company \"{buyer_name}\" (self-dealing).",
                        severity=Severity.ERROR,
                        evidence={"name": buyer_name},
                        invoice_index=i,
                        invoice_number=number,
                        field="buyer.name",
                    )
                )

            for party, role in ((buyer, "buyer"), (seller, "seller")):
                tax_id = (party.tax_id or "").strip()
                if not tax_id:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: {role} tax id is missing.",
                            severity=Severity.WARNING,
                            evidence={},
                            invoice_index=i,
                            invoice_number=number,
                            field=f"{role}.tax_id",
                        )
                    )
                elif not _TAX_ID_RE.match(tax_id):
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: {role} tax id \"{tax_id}\" "
                            "has an invalid format (expected 15-20 "
                            "alphanumeric characters).",
                            severity=Severity.ERROR,
                            evidence={"tax_id": tax_id},
                            invoice_index=i,
                            invoice_number=number,
                            field=f"{role}.tax_id",
                        )
                    )
                elif len(tax_id) == 18 and not _uscc_checksum_ok(tax_id):
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: {role} tax id \"{tax_id}\" "
                            "fails the GB 32100-2015 checksum (possible OCR "
                            "error or forged document).",
                            severity=Severity.WARNING,
                            evidence={"tax_id": tax_id},
                            invoice_index=i,
                            invoice_number=number,
                            field=f"{role}.tax_id",
                        )
                    )

            buyer_tax = (buyer.tax_id or "").strip()
            seller_tax = (seller.tax_id or "").strip()
            if buyer_tax and seller_tax and buyer_tax == seller_tax:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: buyer and seller share the same "
                        f"tax id \"{buyer_tax}\".",
                        severity=Severity.ERROR,
                        evidence={"tax_id": buyer_tax},
                        invoice_index=i,
                        invoice_number=number,
                        field="buyer.tax_id",
                    )
                )
        return findings
