"""Line-item sums vs. declared totals consistency."""

from __future__ import annotations

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule


class LineItemsSumRule(AuditRule):
    rule_id = "line_items_sum"
    name = "Line-item sum consistency"
    description = (
        "Checks that the sum of line-item amounts (and taxes) matches the "
        "declared document totals, within a tolerance that scales with the "
        "number of line items (per-line rounding accumulates)."
    )
    default_severity = Severity.WARNING

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []

        for i, doc in enumerate(batch):
            if not doc.items:
                continue
            tolerance = self.settings.arith_tolerance * max(1, len(doc.items))
            number = doc.invoice_number

            sum_amount = sum(
                it.amount_excluding_tax for it in doc.items if it.amount_excluding_tax is not None
            )
            sum_tax = sum(it.tax_amount for it in doc.items if it.tax_amount is not None)

            if doc.amount_excluding_tax is not None and sum_amount > 0:
                diff = abs(sum_amount - doc.amount_excluding_tax)
                if diff > tolerance:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: line items sum to "
                            f"{sum_amount:.2f} but declared amount is "
                            f"{doc.amount_excluding_tax:.2f} (diff {diff:.2f}).",
                            severity=Severity.WARNING,
                            evidence={
                                "line_sum": sum_amount,
                                "declared": doc.amount_excluding_tax,
                                "diff": diff,
                                "tolerance": tolerance,
                                "line_count": len(doc.items),
                            },
                            invoice_index=i,
                            invoice_number=number,
                            field="amount_excluding_tax",
                        )
                    )

            if doc.tax_amount is not None and sum_tax > 0:
                diff = abs(sum_tax - doc.tax_amount)
                if diff > tolerance:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: line taxes sum to "
                            f"{sum_tax:.2f} but declared tax is "
                            f"{doc.tax_amount:.2f} (diff {diff:.2f}).",
                            severity=Severity.WARNING,
                            evidence={
                                "line_tax_sum": sum_tax,
                                "declared": doc.tax_amount,
                                "diff": diff,
                                "tolerance": tolerance,
                                "line_count": len(doc.items),
                            },
                            invoice_index=i,
                            invoice_number=number,
                            field="tax_amount",
                        )
                    )
        return findings
