"""Field-level evaluation metrics used by the benchmark."""

from __future__ import annotations

from app.models.invoice import InvoiceDocument

_FIELD_SPECS: list[tuple[str, str]] = [
    ("invoice_number", "str"),
    ("issue_date", "date"),
    ("buyer.name", "str"),
    ("buyer.tax_id", "str"),
    ("seller.name", "str"),
    ("seller.tax_id", "str"),
    ("amount_excluding_tax", "num"),
    ("tax_amount", "num"),
    ("amount_including_tax", "num"),
]


def _get_value(doc: InvoiceDocument, path: str):
    if "." in path:
        part, rest = path.split(".", 1)
        obj = getattr(doc, part, None)
        return getattr(obj, rest, None) if obj is not None else None
    return getattr(doc, path, None)


def _norm(value, kind: str):
    if value is None:
        return None
    if kind == "str":
        return str(value).strip()
    if kind == "date":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return value


def compare_documents(
    gt: InvoiceDocument, pred: InvoiceDocument, tolerance: float = 0.02
) -> dict:
    """Per-field comparison of one ground-truth doc against one prediction."""
    result: dict = {}
    for path, kind in _FIELD_SPECS:
        g = _norm(_get_value(gt, path), kind)
        p = _norm(_get_value(pred, path), kind)
        confidence = pred.confidence.get(path, 1.0)

        if g is None or (kind == "str" and g == ""):
            matched = True  # nothing to verify
            compared = False
        elif p is None or (kind == "str" and p == ""):
            matched = False
            compared = True
        elif kind == "num":
            matched = abs(g - p) <= tolerance
            compared = True
        else:
            matched = g == p
            compared = True

        result[path] = {
            "match": matched,
            "compared": compared,
            "confidence": confidence,
            "gt": g,
            "pred": p,
        }
    return result


def field_accuracy_report(
    gt_docs: list[InvoiceDocument],
    pred_docs: list[InvoiceDocument],
    tolerance: float = 0.02,
) -> dict:
    """Aggregate per-field accuracy and mean confidence over a batch."""
    fields: dict = {}
    for path, kind in _FIELD_SPECS:
        correct = compared = 0
        conf_sum = 0.0
        for gt, pred in zip(gt_docs, pred_docs):
            comp = compare_documents(gt, pred, tolerance)[path]
            if comp["compared"]:
                compared += 1
                correct += int(comp["match"])
            conf_sum += comp["confidence"]
        n_docs = len(pred_docs)
        fields[path] = {
            "accuracy": round(correct / compared, 4) if compared else 1.0,
            "correct": correct,
            "compared": compared,
            "avg_confidence": round(conf_sum / n_docs, 4) if n_docs else 1.0,
        }

    overall_accuracy = (
        sum(f["accuracy"] for f in fields.values()) / len(fields) if fields else 1.0
    )
    report = dict(fields)
    report["overall"] = {
        "accuracy": round(overall_accuracy, 4),
        "fields": len(fields),
    }
    report["_meta"] = {"tolerance": tolerance}
    return report
