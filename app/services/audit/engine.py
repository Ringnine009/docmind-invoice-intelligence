"""The audit engine: runs every registered rule over a batch."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument
from app.services.audit.base import AuditRule, _RULE_REGISTRY


@dataclass(frozen=True)
class RuleError:
    """A rule that raised while evaluating the batch.

    Recorded rather than propagated: one broken rule must never discard the
    findings every *other* rule produced, because an empty findings list reads
    as "this batch is clean" — the false negative an audit product exists to
    avoid. The error is surfaced to the caller instead, and it makes the audit
    non-conclusive.
    """

    rule_id: str
    rule_name: str
    error: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "error": self.error,
        }


class AuditEngine:
    """Batch audit engine.

    Instantiates every registered rule with the current settings, runs each
    over the batch, and returns findings sorted by severity (most severe
    first). Rules can be excluded via ``disabled_rules`` or replaced wholesale
    via ``rules`` (dependency injection for tests).

    Each rule is executed in isolation: a rule that raises is skipped and its
    failure is reported through :attr:`errors` (checked after :meth:`run`),
    while the remaining rules keep producing findings.
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
        #: Populated by :meth:`run` — never accumulated across runs.
        self.errors: list[RuleError] = []

    def run(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        self.errors = []
        for rule in self.rules:
            if rule.rule_id in self.disabled:
                continue
            try:
                findings.extend(rule.evaluate(batch))
            except Exception as exc:  # noqa: BLE001 — isolation is the point
                self.errors.append(
                    RuleError(
                        rule_id=rule.rule_id,
                        rule_name=getattr(rule, "name", "") or rule.rule_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        findings.sort(key=lambda f: f.severity, reverse=True)
        return findings

    @staticmethod
    def summarize(
        findings: list[AuditFinding], documents_audited: int | None = None
    ) -> dict:
        summary: dict = {"total": len(findings), "by_severity": {}}
        for severity in Severity:
            summary["by_severity"][severity.name] = 0
        for finding in findings:
            summary["by_severity"][finding.severity.name] += 1
        if documents_audited is not None:
            # How many documents the findings were derived from. Zero findings
            # over zero documents is "nothing was audited", not "all clear" —
            # downstream consumers need to be able to tell those apart.
            summary["documents_audited"] = documents_audited
        return summary
