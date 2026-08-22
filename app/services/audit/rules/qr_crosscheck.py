"""QR-code cross-check: decoded QR payload vs. extracted fields.

The QR code on a Chinese e-invoice encodes the invoice number, total amount
and issue date. Because it is machine-printed and checksum-free but hard to
tamper with silently, comparing it against the vision-model extraction is one
of the strongest signals we have: a mismatch usually means OCR error or
document manipulation.
"""

from __future__ import annotations

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule
from app.services.extraction.qr_utils import parse_qr_payload


class QrCrosscheckRule(AuditRule):
    rule_id = "qr_crosscheck"
    name = "QR code cross-check"
    description = (
        "Decodes the invoice QR payload (number / total / date) and compares "
        "it with the extracted fields; mismatches indicate OCR errors or "
        "possible document manipulation."
    )
    default_severity = Severity.ERROR

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        tolerance = self.settings.arith_tolerance
        findings: list[AuditFinding] = []

        for i, doc in enumerate(batch):
            if not doc.qr_payload:
                continue
            number = doc.invoice_number
            parsed = parse_qr_payload(doc.qr_payload)
            if parsed is None:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: QR payload could not be parsed "
                        f"(raw: {doc.qr_payload[:40]!r}).",
                        severity=Severity.WARNING,
                        evidence={"qr_payload": doc.qr_payload},
                        invoice_index=i,
                        invoice_number=number,
                        field="qr_payload",
                    )
                )
                continue

            if parsed.number and number and parsed.number != number:
                findings.append(
                    self.finding(
                        f"Invoice {number}: QR code encodes invoice number "
                        f"{parsed.number}, which differs from the extracted "
                        f"number (possible tampering or OCR error).",
                        severity=Severity.ERROR,
                        evidence={
                            "qr_number": parsed.number,
                            "extracted_number": number,
                            "qr_payload": doc.qr_payload,
                        },
                        invoice_index=i,
                        invoice_number=number,
                        field="invoice_number",
                    )
                )

            if parsed.amount and doc.amount_including_tax is not None:
                diff = abs(parsed.amount - doc.amount_including_tax)
                if diff > tolerance:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: QR code encodes total "
                            f"¥{parsed.amount:.2f} but the extracted total is "
                            f"¥{doc.amount_including_tax:.2f} (diff ¥{diff:.2f}).",
                            severity=Severity.ERROR,
                            evidence={
                                "qr_amount": parsed.amount,
                                "extracted_amount": doc.amount_including_tax,
                                "diff": diff,
                                "qr_payload": doc.qr_payload,
                            },
                            invoice_index=i,
                            invoice_number=number,
                            field="amount_including_tax",
                        )
                    )

            if parsed.date and doc.issue_date is not None and parsed.date != doc.issue_date:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: QR code encodes issue date "
                        f"{parsed.date.isoformat()} but the extracted date is "
                        f"{doc.issue_date.isoformat()}.",
                        severity=Severity.WARNING,
                        evidence={
                            "qr_date": parsed.date.isoformat(),
                            "extracted_date": doc.issue_date.isoformat(),
                            "qr_payload": doc.qr_payload,
                        },
                        invoice_index=i,
                        invoice_number=number,
                        field="issue_date",
                    )
                )
        return findings
