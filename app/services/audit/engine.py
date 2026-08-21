"""The audit engine: runs every registered rule over a batch."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule, _RULE_REGISTRY


class AuditEngine:
    """Batch audit engine.

    Instantiates every registered rule with the current settings, runs each
    over the batch, and returns findings sorted by severity (most severe
    first). Rules can be excluded via ``disabled_rules`` or replaced wholesale
    via ``rules`` (dependency injection for tests).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        disabled_rules: set[str] | None = None,
        rules: list[AuditRule] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.disabled = set(disabled_rules or [])
        self.rules = rules if rules is not None else [
            cls(self.settings) for cls in _RULE_REGISTRY.values()
        ]

    def run(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for rule in self.rules:
            if rule.rule_id in self.disabled:
                continue
            findings.extend(rule.evaluate(batch))
        findings.sort(key=lambda f: f.severity, reverse=True)
        return findings

    @staticmethod
    def summarize(findings: list[AuditFinding]) -> dict:
        summary: dict = {"total": len(findings), "by_severity": {}}
        for severity in Severity:
            summary["by_severity"][severity.name] = 0
        for finding in findings:
            summary["by_severity"][finding.severity.name] += 1
        return summary
