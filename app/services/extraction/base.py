"""Extractor abstraction and shared error type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.invoice import InvoiceDocument


class ExtractionError(Exception):
    """Raised when an invoice could not be extracted."""


class UnusableResponseError(ExtractionError):
    """A model answered, but its answer could not be turned into an invoice.

    Distinct from :class:`ModelUnavailableError`: text arrived, so it can be
    handed back to the model together with the reason it was rejected.
    """

    def __init__(self, message: str, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text


class ModelUnavailableError(ExtractionError):
    """The model refused or could not be reached (quota, auth, outage).

    No document was read and there is nothing to correct, so the honest report
    is "unavailable" — not "tried and produced nothing", which reads as if the
    fallback had actually looked at the invoice. The end-to-end evaluation in
    ``docs/e2e-eval.md`` had a fallback that returned 403 for the whole run;
    counting it as an attempt hid why 12.2% of invoices went unread.
    """


class Extractor(ABC):
    """Converts an invoice PDF into a structured :class:`InvoiceDocument`."""

    name: str = "base"

    @abstractmethod
    def extract(self, file_path: str | Path) -> InvoiceDocument:
        """Extract structured fields from an invoice file."""
