"""Helpers for turning raw (possibly Chinese-keyed) LLM output into typed
:class:`InvoiceDocument` instances, plus robust JSON extraction.

Extraction is a lossy pipeline with no schema guarantee at the model end, so
:func:`extract_json` has to decide what a *broken* response meant. It does that
with a ladder of increasingly specific repairs, cheapest and least invasive
first, and every rung has to produce something :func:`json.loads` accepts — no
rung may invent a value. The two shapes that actually occur in production are
recorded in ``tests/fixtures/model_responses/`` and are explained in
:func:`_scan`:

* the outermost object is never closed (the model rambles instead of writing
  the final ``}``), and
* the model re-emits members it already emitted and is cut off mid-string,
  which makes the naive ``text[first "{"] : text[last "}"]`` slice glue two
  fragments together.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

from app.models.invoice import InvoiceDocument, InvoiceItem, InvoiceParty

# Chinese invoice field names → English document field names.
_CHINESE_FIELD_MAP: dict[str, str] = {
    "发票类型": "invoice_type",
    "发票号码": "invoice_number",
    "开票日期": "issue_date",
    "购买方名称": "buyer.name",
    "购买方税号": "buyer.tax_id",
    "销售方名称": "seller.name",
    "销售方税号": "seller.tax_id",
    "项目明细": "items",
    "金额": "amount_excluding_tax",
    "税额": "tax_amount",
    "价税合计小写": "amount_including_tax",
    "价税合计大写": "amount_in_words",
    "校验码": "check_code",
    "开票人": "issuer",
    "备注": "remarks",
    "置信度": "confidence",
    "二维码内容": "qr_payload",
    "qr_payload": "qr_payload",
}

_CHINESE_ITEM_MAP: dict[str, str] = {
    "项目名称": "name",
    "规格型号": "specification",
    "单位": "unit",
    "数量": "quantity",
    "单价": "unit_price",
    "金额": "amount_excluding_tax",
    "税率": "tax_rate",
    "税额": "tax_amount",
}

_PARTY_SUB_MAP: dict[str, str] = {
    "name": "name",
    "名称": "name",
    "tax_id": "tax_id",
    "税号": "tax_id",
}

_ALLOWED_DOC_FIELDS = {
    "invoice_type",
    "invoice_number",
    "issue_date",
    "amount_excluding_tax",
    "tax_amount",
    "amount_including_tax",
    "amount_in_words",
    "remarks",
    "issuer",
    "check_code",
    "qr_payload",
}

_NUMERIC_FIELDS = {"amount_excluding_tax", "tax_amount", "amount_including_tax"}


def _first_occurrence_wins(pairs: list[tuple[str, Any]]) -> dict:
    """Build an object keeping the **first** value of a repeated key.

    ``json`` keeps the last one, which is wrong for the defect this module
    exists to repair. When a vision model degenerates it repeats part of its
    output — and what it repeats is often the *skeleton* it was given
    (``"项目名称": "", "数量": 0, "金额": 0``), emitted out of place as top-level
    keys. Last-wins lets that echo overwrite the number the model had already
    read correctly, turning a good read into a confident zero — strictly worse
    than the parse failure it replaced. The repetition is degeneration, not a
    correction, so the first occurrence is the reading.
    """
    out: dict = {}
    for key, value in pairs:
        if key not in out:
            out[key] = value
    return out


def _decode(text: str) -> dict:
    """``json.loads`` with repair-friendly duplicate-key semantics."""
    return json.loads(text, object_pairs_hook=_first_occurrence_wins)


class MalformedJSONError(ValueError):
    """No usable JSON object could be recovered from a model response.

    A :class:`ValueError` because that is the contract callers already catch,
    extended with the raw text and the failing position so the extractor can
    hand the model back its own defect instead of blindly asking again.
    """

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


#: Repairs applied on top of a candidate payload, in application order. They fix
#: punctuation and stray control characters only — never a missing value.
def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _strip_control_chars(text: str) -> str:
    return "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")


_VARIANTS = (
    ("", lambda text: text),
    ("+trailing-commas-stripped", _strip_trailing_commas),
    ("+control-chars-stripped", _strip_control_chars),
    ("+both", lambda text: _strip_control_chars(_strip_trailing_commas(text))),
)

_WHITESPACE = " \t\r\n"
_LITERAL = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")

# --- the scanner -----------------------------------------------------------

#: What the innermost container expects next.
_VALUE, _KEY, _COLON, _COMMA = "value", "key", "colon", "comma"
_CLOSER = {"{": "}", "[": "]"}


@dataclass(frozen=True)
class _Scan:
    """Where a candidate payload stopped being structurally valid."""

    #: The input ran out while containers were still open, or inside a string:
    #: the response was **truncated** and closing it is a faithful repair.
    truncated: bool
    #: The outermost object was closed before the input ran out.
    complete: bool
    #: Offset just after the last *complete* outermost-object member, or None.
    #: Everything before it survived the model intact; whatever follows is the
    #: unterminated tail and carries no value yet.
    last_member_end: int | None
    reason: str


def _string_end(text: str, start: int) -> int | None:
    """Index just after the string starting at ``start``, or None if unterminated."""
    i, n = start + 1, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            return i + 1
        if ch < " ":  # a raw control character is never legal inside a string
            return None
        i += 1
    return None


def _scan(payload: str) -> _Scan:
    """Walk ``payload`` and report *how* it fails, not just that it does.

    The distinction that matters is truncation versus contradiction. A payload
    that ends while its containers are still open can be closed without losing
    information — the document body was emitted. A payload that is structurally
    wrong *inside* (``{"a": 1, "b": }``) cannot: any repair would have to guess
    at a value, so the caller must refuse it instead.
    """
    stack: list[tuple[str, str]] = []
    expect = _VALUE
    last_member_end: int | None = None
    i, n = 0, len(payload)

    while i < n:
        ch = payload[i]
        if ch in _WHITESPACE:
            i += 1
            continue

        # A closer is checked first: it is legal in every state that is not
        # inside a string, including right after a trailing comma.
        if ch in "}]":
            if not stack or _CLOSER[stack[-1][0]] != ch:
                return _Scan(False, False, last_member_end, f"unexpected {ch!r} at char {i}")
            stack.pop()
            i += 1
            if not stack:
                return _Scan(False, True, last_member_end, "the outermost object is closed")
            if len(stack) == 1:
                last_member_end = i
            expect = _COMMA
            continue

        if expect == _KEY:
            if ch != '"':
                return _Scan(False, False, last_member_end, f"expected a key at char {i}")
            end = _string_end(payload, i)
            if end is None:
                return _Scan(True, False, last_member_end, f"unterminated key at char {i}")
            i, expect = end, _COLON
            continue

        if expect == _COLON:
            if ch != ":":
                return _Scan(False, False, last_member_end, f"expected ':' at char {i}")
            i, expect = i + 1, _VALUE
            continue

        if ch == ",":
            if expect != _COMMA:
                return _Scan(False, False, last_member_end, f"unexpected ',' at char {i}")
            i += 1
            expect = _KEY if stack and stack[-1][0] == "{" else _VALUE
            continue

        if ch == '"':
            end = _string_end(payload, i)
            if end is None:
                return _Scan(True, False, last_member_end, f"unterminated string at char {i}")
            i = end
            if len(stack) == 1:
                last_member_end = i
            expect = _COMMA
            continue

        if ch in "{[":
            stack.append((ch, _KEY if ch == "{" else _VALUE))
            i += 1
            expect = _KEY if ch == "{" else _VALUE
            continue

        literal = _LITERAL.match(payload, i)
        if literal is None:
            return _Scan(False, False, last_member_end, f"unexpected {ch!r} at char {i}")
        i = literal.end()
        if len(stack) == 1:
            last_member_end = i
        expect = _COMMA

    return _Scan(bool(stack), not stack, last_member_end, "input ended")


def _first_complete_object(payload: str) -> str | None:
    """The first well-formed JSON object, with anything after it discarded.

    Covers the echo shape: a complete document followed by a second one, or by
    repeated fragments, which the naive slice merges into invalid JSON.
    """
    try:
        value, end = json.JSONDecoder().raw_decode(payload)
    except ValueError:
        return None
    return payload[:end] if isinstance(value, dict) else None


def _closed_truncation(payload: str) -> str | None:
    """Close a payload the model never finished, dropping only the open tail.

    Returns None unless the payload is genuinely truncated *and* at least one
    outermost member was completed — so ``{"a": }`` (contradictory, not
    truncated) and ``{`` (nothing to keep) are both refused rather than guessed
    at. Every complete member survives; only the unterminated one is dropped,
    and a dropped member carries no value, so nothing is fabricated.
    """
    scan = _scan(payload)
    if not scan.truncated or scan.last_member_end is None:
        return None
    head = payload[: scan.last_member_end].rstrip().rstrip(",").rstrip()
    # ``last_member_end`` marks a value completed while exactly one container
    # was open, so a single brace closes the document.
    return head + "}" if head else None


def _candidates(payload: str, sliced: str) -> Iterator[tuple[str, str]]:
    """Yield ``(json_text, strategy)`` pairs, cheapest and least invasive first."""
    bases = (
        ("verbatim", sliced),
        ("first-complete-object", _first_complete_object(payload)),
        ("truncated-tail-closed", _closed_truncation(payload)),
    )
    seen: set[str] = set()
    for strategy, base in bases:
        if base is None:
            continue
        for suffix, repair in _VARIANTS:
            text = repair(base)
            if text in seen:
                continue
            seen.add(text)
            yield text, strategy + suffix


def extract_json_verbose(text: str) -> tuple[dict, str]:
    """Like :func:`extract_json`, but also report which repair was needed.

    The strategy name is what makes the repair path observable: the extractor
    records it on the document, so an evaluation can count how many documents
    needed *any* repair instead of inferring it from the absence of failures.
    ``"verbatim"`` means the response was already well-formed.
    """
    if not text or not text.strip():
        raise MalformedJSONError("empty model response", raw=text or "")

    body = text
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    start = body.find("{")
    if start == -1:
        raise MalformedJSONError("no JSON object found in model response", raw=text)

    payload = body[start:]
    end = payload.rfind("}")
    sliced = payload[: end + 1] if end != -1 else payload

    errors: list[str] = []
    for candidate, strategy in _candidates(payload, sliced):
        try:
            data = _decode(candidate)
        except json.JSONDecodeError as exc:
            errors.append(f"{strategy}: {exc}")
            continue
        if isinstance(data, dict):
            return data, strategy
        errors.append(f"{strategy}: top-level JSON value is not an object")

    scan = _scan(payload)
    first = errors[0].split(": ", 1)[1] if errors else "no candidate parsed"
    raise MalformedJSONError(
        f"malformed JSON in model response: {first} "
        f"({len(errors)} recovery attempt(s); {scan.reason})",
        raw=text,
    )


def extract_json(text: str) -> dict:
    """Extract the first JSON object from a model response.

    Tolerates the JSON defects a vision model actually produces: code fences,
    prose around the object, trailing commas, stray control characters, a
    missing final ``}``, and a repeated tail. Values are never invented — a
    response whose body is missing is rejected, not guessed at.
    """
    return extract_json_verbose(text)[0]


def _to_float(value: Any) -> float | None:
    """Parse a loose numeric string ('￥199.00', '13%', 884.07) to float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("￥", "")
        .replace("¥", "")
        .replace("%", "")
        .replace("元", "")
    )
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in {".", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    m = re.match(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_item(item_raw: dict) -> InvoiceItem:
    data: dict[str, Any] = {}
    for key, value in item_raw.items():
        field = _CHINESE_ITEM_MAP.get(key, key)
        if field in {"quantity", "unit_price", "amount_excluding_tax", "tax_rate", "tax_amount"}:
            data[field] = _to_float(value)
        else:
            data[field] = _clean_str(value)
    name = data.get("name")
    if not name:
        data["name"] = "—"
    return InvoiceItem.model_validate(data)


def normalize_raw_invoice(raw: dict) -> InvoiceDocument:
    """Map a raw LLM output dict (Chinese or English keys) to a typed doc."""
    data: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    items: list[InvoiceItem] = []
    buyer: dict[str, Any] = {}
    seller: dict[str, Any] = {}

    for key, value in raw.items():
        field = _CHINESE_FIELD_MAP.get(key, key)

        if field == "confidence":
            if isinstance(value, dict):
                for conf_key, conf_value in value.items():
                    mapped = _CHINESE_FIELD_MAP.get(conf_key, conf_key)
                    parsed = _to_float(conf_value)
                    if parsed is not None:
                        confidence[mapped] = max(0.0, min(1.0, parsed))
        elif field == "items":
            if isinstance(value, list):
                items = [_normalize_item(it) for it in value if isinstance(it, dict)]
        elif field in {"buyer", "seller"}:
            target = buyer if field == "buyer" else seller
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    mapped = _PARTY_SUB_MAP.get(sub_key, sub_key)
                    target[mapped] = _clean_str(sub_value)
        elif field.startswith("buyer.") or field.startswith("seller."):
            target = buyer if field.startswith("buyer.") else seller
            target[field.split(".", 1)[1]] = _clean_str(value)
        elif field in _ALLOWED_DOC_FIELDS:
            if field == "issue_date":
                data[field] = _parse_date(value)
            elif field in _NUMERIC_FIELDS:
                data[field] = _to_float(value)
            else:
                data[field] = _clean_str(value) if value is not None else None
        # unknown keys are ignored (forward compatibility)

    return InvoiceDocument(
        **data,
        buyer=InvoiceParty(**buyer) if buyer else InvoiceParty(),
        seller=InvoiceParty(**seller) if seller else InvoiceParty(),
        items=items,
        confidence=confidence,
    )


#: Fields an audit rule can actually read. A payload that maps to none of them
#: is not a partial invoice — it is a non-answer.
_AUDITABLE_ATTRS = (
    "invoice_number",
    "amount_excluding_tax",
    "tax_amount",
    "amount_including_tax",
)


def has_auditable_content(doc: InvoiceDocument) -> bool:
    """Whether a normalized document carries anything an audit rule can read.

    Guards the JSON repair path. Closing a truncated brace can turn "the model
    never emitted the document" into a syntactically perfect, entirely empty
    invoice; accepting that as a *successful* extraction would report a read
    that never happened — the one failure a repair must not create. Reporting
    the response as unusable and asking again is both cheaper and honest.
    """
    if doc.items:
        return True
    if any(getattr(doc, attr) not in (None, "", 0) for attr in _AUDITABLE_ATTRS):
        return True
    return bool(
        doc.buyer.name or doc.buyer.tax_id or doc.seller.name or doc.seller.tax_id
    )
