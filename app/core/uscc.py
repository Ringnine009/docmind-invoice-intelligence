"""GB 32100-2015 unified social credit code (统一社会信用代码) helpers."""

from __future__ import annotations

_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"  # no I/O/Z/S/V
_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]

# 18-char unified code; legacy tax ids are 15/20 digits.
TAX_ID_PATTERN = r"^[0-9A-Z]{15,20}$"


def uscc_check_char(code17: str) -> str:
    """Compute the GB 32100-2015 check character for the first 17 chars."""
    total = sum(_WEIGHTS[i] * _CHARSET.index(ch) for i, ch in enumerate(code17))
    return _CHARSET[(31 - total % 31) % 31]


def uscc_checksum_ok(code: str) -> bool:
    """Validate the check character of an 18-char USCC."""
    if len(code) != 18 or any(ch not in _CHARSET for ch in code):
        return False
    return uscc_check_char(code[:17]) == code[17]


def repair_uscc(tax_id: str | None) -> tuple[str | None, bool]:
    """Try to repair an 18-char USCC whose check character is wrong.

    If the first 17 characters are valid charset characters, returns the
    corrected code and ``changed=True``; otherwise returns the input
    unchanged with ``changed=False`` (cannot repair — likely OCR mangled the
    body itself).
    """
    if not tax_id:
        return tax_id, False
    code = tax_id.strip().upper()
    if len(code) != 18:
        return tax_id, False
    if all(ch in _CHARSET for ch in code[:17]) and not uscc_checksum_ok(code):
        return code[:17] + uscc_check_char(code[:17]), True
    return tax_id, False
