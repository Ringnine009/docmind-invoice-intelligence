"""Extractor abstraction and shared error type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.invoice import InvoiceDocument


class ExtractionError(Exception):
    """Raised when an invoice could not be extracted."""


class Extractor(ABC):
    """Converts an invoice PDF into a structured :class:`InvoiceDocument`."""

    name: str = "base"

    @abstractmethod
    def extract(self, file_path: str | Path) -> InvoiceDocument:
        """Extract structured fields from an invoice file."""
