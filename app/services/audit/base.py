"""Audit rule base class and the rule registry.

New rules are added by subclassing :class:`AuditRule` and defining a unique
``rule_id``; the metaclass hook registers them automatically, so the engine
and the ``/api/rules`` endpoint pick them up with zero wiring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.models.audit import AuditFinding, Severity
from app.models.invoice import InvoiceDocument

if TYPE_CHECKING:
    from app.core.config import Settings

_RULE_REGISTRY: dict[str, type["AuditRule"]] = {}


class AuditRule(ABC):
    """Base class for a single audit rule operating over an invoice batch."""

    rule_id: str = ""
    name: str = ""
    description: str = ""
    default_severity: Severity = Severity.WARNING

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.rule_id and cls is not AuditRule:
            _RULE_REGISTRY[cls.rule_id] = cls

    def __init__(self, settings: "Settings | None" = None) -> None:
        from app.core.config import get_settings

        self.settings = settings or get_settings()

    @abstractmethod
    def evaluate(self, batch: list[InvoiceDocument]) -> list[AuditFinding]:
        """Return findings for this rule over the whole batch."""

    def finding(
        self,
        message: str,
        severity: Severity | None = None,
        *,
        evidence: dict | None = None,
        invoice_index: int | None = None,
        invoice_number: str | None = None,
        field: str | None = None,
    ) -> AuditFinding:
        return AuditFinding(
            rule_id=self.rule_id,
            rule_name=self.name,
            severity=severity if severity is not None else self.default_severity,
            message=message,
            evidence=evidence or {},
            invoice_index=invoice_index,
            invoice_number=invoice_number,
            field=field,
        )


def get_registered_rules() -> dict[str, AuditRule]:
    """Instantiate one copy of every registered rule (caller may configure)."""
    return {rid: cls() for rid, cls in _RULE_REGISTRY.items()}


def list_rule_metadata() -> list[dict]:
    """Metadata for every registered rule (used by /api/rules and the UI)."""
    return [
        {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "description": rule.description,
            "default_severity": rule.default_severity.name,
        }
        for rule in get_registered_rules().values()
    ]


# Importing the built-in rules package triggers their registration.
from app.services.audit import rules as _rules  # noqa: E402, F401
