"""Duplicate-invoice-number detection (batch-level)."""

from __future__ import annotations

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule


class DuplicateInvoiceNumberRule(AuditRule):
    rule_id = "dup_invoice_number"
    name = "Duplicate invoice number"
    description = (
        "Flags invoice numbers that appear more than once within a batch, "
        "a common indicator of duplicate reimbursement."
    )
    default_severity = Severity.CRITICAL

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        groups: dict[str, list[int]] = {}
        for i, doc in enumerate(batch):
            number = (doc.invoice_number or "").strip()
            if number:
                groups.setdefault(number, []).append(i)

        findings: list[AuditFinding] = []
        for number, indices in groups.items():
            if len(indices) > 1:
                findings.append(
                    self.finding(
                        f"Invoice number {number} appears {len(indices)} times in "
                        "this batch (possible duplicate reimbursement).",
                        severity=Severity.CRITICAL,
                        evidence={
                            "number": number,
                            "count": len(indices),
                            "invoice_indices": indices,
                        },
                        invoice_number=number,
                        field="invoice_number",
                    )
                )
        return findings
