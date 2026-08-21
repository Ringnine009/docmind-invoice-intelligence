"""Low model-confidence flags (ties the audit engine to extraction quality)."""

from __future__ import annotations

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule


class LowConfidenceRule(AuditRule):
    rule_id = "low_confidence"
    name = "Low extraction confidence"
    description = (
        "Flags fields whose model confidence is below the configured "
        "threshold, so downstream reviewers can double-check them."
    )
    default_severity = Severity.WARNING

    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        threshold = self.settings.low_confidence_threshold
        findings: list[AuditFinding] = []

        for i, doc in enumerate(batch):
            low = sorted(
                field for field, conf in doc.confidence.items() if conf < threshold
            )
            if low:
                findings.append(
                    self.finding(
                        f"Invoice {doc.invoice_number or i}: low model "
                        f"confidence (< {threshold:g}) on fields: "
                        f"{', '.join(low)}.",
                        severity=Severity.WARNING,
                        evidence={"fields": low, "threshold": threshold},
                        invoice_index=i,
                        invoice_number=doc.invoice_number,
                    )
                )
        return findings
