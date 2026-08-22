"""QR-code decoding and payload parsing for Chinese e-invoices.

Chinese e-invoice QR codes encode a compact payload such as::

    01,32,,24417000000034170288,199.00,20240720

positionally: (version, type, blank, invoice number, total amount, date
YYYYMMDD). The payload is the single most tamper-resistant field on the
invoice, so DocMind decodes it and cross-checks it against the vision-model
extraction (audit rule ``qr_crosscheck``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class QrPayload:
    number: str
    amount: float
    date: date | None = None
    raw: str = ""


def decode_qr(image_bytes: bytes) -> str | None:
    """Decode the QR code from an image (PNG bytes). Returns the raw string."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    nparr = np.frombuffer(image_bytes, np.uint8)
    try:
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except cv2.error:
        return None
    if image is None:
        return None
    try:
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    except cv2.error:
        return None
    return data or None


def parse_qr_payload(payload: str) -> QrPayload | None:
    """Parse a QR payload into structured fields; None when unparseable."""
    if not payload or not payload.strip():
        return None

    parts = [p.strip() for p in payload.split(",")]
    # Positional layout: 01,32,,<number>,<amount>,<yyyymmdd>[,...]
    number = None
    amount = None
    raw_date = None
    if len(parts) >= 5:
        number = parts[3] if re.fullmatch(r"\d{20}", parts[3]) else None
        amount = _parse_amount(parts[4])
        if len(parts) >= 6:
            raw_date = parts[5]

    # Defensive fallback: regex scan of the whole payload.
    if number is None:
        m = re.search(r"\d{20}", payload)
        number = m.group(0) if m else None
    if amount is None:
        m = re.search(r"\d+\.\d{2}", payload)
        amount = float(m.group(0)) if m else None

    parsed_date = _parse_yyyymmdd(raw_date) if raw_date else None
    if parsed_date is None:
        m = re.search(r"(20\d{2})(\d{2})(\d{2})", payload)
        if m:
            try:
                parsed_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                parsed_date = None

    if number is None and amount is None:
        return None
    return QrPayload(number=number or "", amount=amount or 0.0, date=parsed_date, raw=payload)


def _parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(str(value).strip()), 2)
    except (TypeError, ValueError):
        return None


def _parse_yyyymmdd(value: str) -> date | None:
    if not re.fullmatch(r"\d{8}", value):
        return None
    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
