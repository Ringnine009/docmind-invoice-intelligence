"""Issue-date sanity: missing or future dates."""

from __future__ import annotations

from datetime import date, timedelta

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule


class InvoiceDateRule(AuditRule):
    rule_id = "invoice_date"
    name = "Issue-date sanity"
    description = "Warns when the issue date is missing or lies in the future."
    default_severity = Severity.WARNING

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        tomorrow = date.today() + timedelta(days=1)

        for i, doc in enumerate(batch):
            number = doc.invoice_number
            d = doc.issue_date
            if d is None:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: issue date is missing.",
                        severity=Severity.WARNING,
                        evidence={},
                        invoice_index=i,
                        invoice_number=number,
                        field="issue_date",
                    )
                )
            elif d > tomorrow:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: issue date {d.isoformat()} is "
                        "in the future.",
                        severity=Severity.WARNING,
                        evidence={"issue_date": d.isoformat()},
                        invoice_index=i,
                        invoice_number=number,
                        field="issue_date",
                    )
                )
        return findings
