"""Amount arithmetic consistency: total = amount_excluding_tax + tax_amount."""

from __future__ import annotations

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule


class ArithmeticTotalRule(AuditRule):
    rule_id = "arithmetic_total"
    name = "Total arithmetic consistency"
    description = (
        "Verifies that amount_including_tax equals amount_excluding_tax + "
        "tax_amount within a small tolerance."
    )
    default_severity = Severity.ERROR

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        tolerance = self.settings.arith_tolerance
        findings: list[AuditFinding] = []

        for i, doc in enumerate(batch):
            excl, tax, incl = (
                doc.amount_excluding_tax,
                doc.tax_amount,
                doc.amount_including_tax,
            )
            number = doc.invoice_number

            if incl is None:
                if excl is not None or tax is not None:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: total amount is missing, "
                            "arithmetic cannot be verified.",
                            severity=Severity.WARNING,
                            evidence={
                                "amount_excluding_tax": excl,
                                "tax_amount": tax,
                                "amount_including_tax": None,
                            },
                            invoice_index=i,
                            invoice_number=number,
                            field="amount_including_tax",
                        )
                    )
                continue

            if excl is None and tax is None:
                continue

            computed = (excl or 0.0) + (tax or 0.0)
            diff = abs(incl - computed)

            if excl is None or tax is None:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: total is {incl:.2f} but one "
                        "component (amount/tax) is missing.",
                        severity=Severity.WARNING,
                        evidence={
                            "amount_excluding_tax": excl,
                            "tax_amount": tax,
                            "amount_including_tax": incl,
                        },
                        invoice_index=i,
                        invoice_number=number,
                        field="amount_including_tax",
                    )
                )
            elif diff > tolerance:
                findings.append(
                    self.finding(
                        f"Invoice {number or i}: arithmetic mismatch — "
                        f"{excl:.2f} + {tax:.2f} = {computed:.2f} but declared "
                        f"total is {incl:.2f} (diff {diff:.2f}).",
                        severity=Severity.ERROR,
                        evidence={
                            "amount_excluding_tax": excl,
                            "tax_amount": tax,
                            "amount_including_tax": incl,
                            "computed_total": computed,
                            "diff": diff,
                            "tolerance": tolerance,
                        },
                        invoice_index=i,
                        invoice_number=number,
                        field="amount_including_tax",
                    )
                )
        return findings
