"""Helpers for turning raw (possibly Chinese-keyed) LLM output into typed
:class:`InvoiceDocument` instances, plus robust JSON extraction."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

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
}

_NUMERIC_FIELDS = {"amount_excluding_tax", "tax_amount", "amount_including_tax"}


def extract_json(text: str) -> dict:
    """Extract the first JSON object from a model response.

    Tolerates common LLM JSON defects (``` fences, prose around the object,
    trailing commas) by progressively repairing the payload.
    """
    if not text or not text.strip():
        raise ValueError("empty model response")
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    candidate = text[start : end + 1]

    last_error: Exception | None = None
    for repaired in _json_repair_iter(candidate):
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
    raise ValueError(f"malformed JSON in model response: {last_error}")


def _json_repair_iter(text: str):
    """Yield progressively-repaired variants of a JSON payload."""
    yield text
    # strip trailing commas:  ,}  or  ,]
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    if fixed != text:
        yield fixed
    # strip control characters that break json.loads
    stripped = "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")
    if stripped != text:
        yield stripped


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
