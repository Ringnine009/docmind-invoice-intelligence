"""Tax-rate reasonableness: allowed set + implied-rate cross-check."""

from __future__ import annotations

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule

_IMPLIED_RATE_TOLERANCE_PP = 0.5  # percentage points


class TaxRateRule(AuditRule):
    rule_id = "tax_rate"
    name = "Tax rate reasonableness"
    description = (
        "Rejects tax rates outside the legally common set (0/1/3/5/6/9/13%) "
        "and cross-checks the declared rate against the one implied by "
        "line-item amounts and taxes."
    )
    default_severity = Severity.ERROR

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        allowed = set(self.settings.allowed_tax_rates)
        findings: list[AuditFinding] = []

        for i, doc in enumerate(batch):
            number = doc.invoice_number
            for item in doc.items:
                rate = item.tax_rate
                label = item.name or "(unnamed item)"
                if rate is None:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: tax rate is missing for "
                            f"item \"{label}\".",
                            severity=Severity.WARNING,
                            evidence={"item": label, "rate": None},
                            invoice_index=i,
                            invoice_number=number,
                            field="items.tax_rate",
                        )
                    )
                    continue

                if rate not in allowed:
                    findings.append(
                        self.finding(
                            f"Invoice {number or i}: tax rate {rate:g}% for item "
                            f"\"{label}\" is not in the allowed set "
                            f"{{{', '.join(f'{r:g}' for r in sorted(allowed))}}}%.",
                            severity=Severity.ERROR,
                            evidence={
                                "item": label,
                                "rate": rate,
                                "allowed": sorted(allowed),
                            },
                            invoice_index=i,
                            invoice_number=number,
                            field="items.tax_rate",
                        )
                    )
                    continue

                # Cross-check implied rate from amounts (only for allowed rates).
                amount = item.amount_excluding_tax
                tax = item.tax_amount
                if amount and tax:
                    implied = round(tax / amount * 100.0, 2)
                    if abs(implied - rate) > _IMPLIED_RATE_TOLERANCE_PP:
                        findings.append(
                            self.finding(
                                f"Invoice {number or i}: declared tax rate "
                                f"{rate:g}% for item \"{label}\" is inconsistent "
                                f"with amounts (implied {implied:g}%).",
                                severity=Severity.ERROR,
                                evidence={
                                    "item": label,
                                    "rate": rate,
                                    "implied": implied,
                                    "amount": amount,
                                    "tax_amount": tax,
                                },
                                invoice_index=i,
                                invoice_number=number,
                                field="items.tax_rate",
                            )
                        )
        return findings
