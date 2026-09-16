"""Audit data models."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field, field_serializer


class Severity(IntEnum):
    """Finding severity, ordered from least to most severe.

    Kept as an ``IntEnum`` so the engine can rank findings with a plain
    comparison; the *wire* format is the member name (see
    :meth:`AuditFinding.serialise_severity`).
    """

    INFO = 0
    WARNING = 1
    ERROR = 2
    CRITICAL = 3


class AuditFinding(BaseModel):
    """A single audit alert produced by a rule."""

    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    invoice_index: int | None = None
    invoice_number: str | None = None
    field: str | None = None

    @field_serializer("severity")
    def serialise_severity(self, severity: Severity) -> str:
        """Emit the severity NAME ("CRITICAL"), not its ordinal.

        The frontend declares ``type Severity = "INFO" | "WARNING" | "ERROR" |
        "CRITICAL"`` and groups findings by that string, and the batch summary's
        ``by_severity`` map already uses these names as keys. Serialising the
        ordinal here instead left the two halves of one response speaking
        different vocabularies: the severity counts rendered while every finding
        list stayed empty (regression test: ``tests/test_severity_contract.py``).
        """
        return severity.name

