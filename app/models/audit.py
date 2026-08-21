"""Audit data models."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


class Severity(IntEnum):
    """Finding severity, ordered from least to most severe."""

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
