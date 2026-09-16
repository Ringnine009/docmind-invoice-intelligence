"""End-to-end evaluation: real vision extraction → audit → scoring.

The field benchmark (``app/services/eval/metrics.py``) scores *reading*; the
audit evaluation (``app/services/eval/audit_metrics.py``) scores *decisions over
labelled fields*. Neither measures the pipeline a user actually gets. This
module supplies the missing pieces:

* :class:`UsageMeter` — per-call token accounting with a hard budget fuse, so a
  real-API run can never quietly spend more than it was allowed to;
* :func:`summarize_rounds` — mean/range across repeated runs, so variance is
  reported instead of a single lucky number;
* :func:`classify_divergences` — why the end-to-end audit result differs from
  the labelled-field result, per invoice and anomaly class, with the concrete
  field values that changed.

Everything here is pure and offline: the API is only touched by
``scripts/run_e2e_eval.py``.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from app.models.invoice import InvoiceDocument
from app.services.eval.audit_metrics import ANOMALY_TO_RULES

SCHEMA_VERSION = 1


# --- pricing ---------------------------------------------------------------


@dataclass(frozen=True)
class ModelPrice:
    """List price for one vision model, in CNY per million tokens.

    Prices are *inputs to the cost model*, not measurements: they are quoted
    with their source so a reader can verify or replace them.
    """

    model: str
    input_cny_per_million: float
    output_cny_per_million: float
    source: str
    assumed: bool = False

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "input_cny_per_million": self.input_cny_per_million,
            "output_cny_per_million": self.output_cny_per_million,
            "source": self.source,
            "assumed": self.assumed,
        }


#: qwen-vl-plus list price, 华北2 (Beijing) region, read 2026-09-16.
QWEN_VL_PLUS_PRICE = ModelPrice(
    model="qwen-vl-plus",
    input_cny_per_million=0.8,
    output_cny_per_million=2.0,
    source=(
        "Alibaba Cloud Model Studio, qwen-vl-plus model page (华北2 北京 list "
        "price): https://help.aliyun.com/zh/model-studio/qwen-vl-plus"
    ),
)

#: Used for any model without a published price in this table. Deliberately
#: priced at the most expensive rate we know, so the budget fuse stays
#: conservative rather than optimistic.
DEFAULT_PRICE = ModelPrice(
    model="<default>",
    input_cny_per_million=QWEN_VL_PLUS_PRICE.input_cny_per_million,
    output_cny_per_million=QWEN_VL_PLUS_PRICE.output_cny_per_million,
    source="fallback assumption: priced as qwen-vl-plus (most expensive rate known)",
    assumed=True,
)

#: Published prices used by the cost model.
PRICING: dict[str, ModelPrice] = {QWEN_VL_PLUS_PRICE.model: QWEN_VL_PLUS_PRICE}


def price_for(
    model: str, pricing: Mapping[str, ModelPrice] | None = None
) -> ModelPrice:
    """List price for ``model``; unknown models get the conservative default."""
    table = PRICING if pricing is None else pricing
    known = table.get(model)
    if known is not None:
        return known
    if model in PRICING:
        return PRICING[model]
    return ModelPrice(
        model=model,
        input_cny_per_million=DEFAULT_PRICE.input_cny_per_million,
        output_cny_per_million=DEFAULT_PRICE.output_cny_per_million,
        source=DEFAULT_PRICE.source,
        assumed=True,
    )


def cost_cny(model: str, prompt_tokens: int, completion_tokens: int,
             pricing: Mapping[str, ModelPrice] | None = None) -> float:
    price = price_for(model, pricing)
    return (
        prompt_tokens / 1_000_000 * price.input_cny_per_million
        + completion_tokens / 1_000_000 * price.output_cny_per_million
    )


# --- budget fuse -----------------------------------------------------------


class BudgetExceeded(RuntimeError):
    """Raised by :meth:`UsageMeter.check_budget` once the budget is spent."""


#: Endpoint hosts that are a product fact rather than an account identifier.
_PUBLIC_ENDPOINT_HOSTS = frozenset(
    {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
)


def redact_endpoint(url: str) -> str:
    """Host part of an API endpoint, redacted when it identifies a deployment.

    The evaluation artifact is committed to a public repository. A dedicated
    MaaS endpoint carries an account-specific subdomain, so only its registrable
    suffix is published; the public DashScope host is kept verbatim because it
    identifies a product, not an account.
    """
    if not url:
        return ""
    host = url.split("//", 1)[-1].split("/", 1)[0]
    host = host.split("@")[-1].split(":", 1)[0]
    if not host or host in _PUBLIC_ENDPOINT_HOSTS:
        return host
    for suffix in ("maas.aliyuncs.com", "aliyuncs.com"):
        if host.endswith(suffix):
            return f"*.{suffix} (dedicated deployment, host redacted)"
    return f"{host} (host kept: not a known dedicated-deployment suffix)"


@dataclass
class CallRecord:
    """One billed model response."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: float
    latency_s: float = 0.0
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_cny": round(self.cost_cny, 8),
            "latency_s": round(self.latency_s, 3),
            "ok": self.ok,
            "error": self.error,
        }


class UsageMeter:
    """Thread-safe token/cost accumulator with a hard budget fuse.

    :meth:`record` never raises: it is called from inside the extractor, whose
    ``except Exception`` handling would otherwise turn a budget abort into an
    ordinary extraction failure. The runner calls :meth:`check_budget` at a
    safe point instead, so exceeding the budget stops the run loudly.
    """

    def __init__(
        self,
        budget_cny: float | None = None,
        pricing: Mapping[str, ModelPrice] | None = None,
    ) -> None:
        self.budget_cny = budget_cny
        self.pricing = dict(pricing) if pricing is not None else None
        self._lock = threading.Lock()
        self.calls: list[CallRecord] = []

    # -- recording ----------------------------------------------------------

    def record(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        *,
        total_tokens: int | None = None,
        latency_s: float = 0.0,
        ok: bool = True,
        error: str | None = None,
    ) -> CallRecord:
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        record = CallRecord(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(
                total_tokens
                if total_tokens is not None
                else prompt_tokens + completion_tokens
            ),
            cost_cny=cost_cny(model, prompt_tokens, completion_tokens, self.pricing),
            latency_s=float(latency_s or 0.0),
            ok=bool(ok),
            error=error,
        )
        with self._lock:
            self.calls.append(record)
        return record

    def recorder(self):
        """A ``recorder(model, usage)`` callable for :class:`DashScopeExtractor`."""

        def _record(model: str, usage: Mapping[str, Any]) -> None:
            self.record(
                model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens"),
                # A refused call (no quota, bad key) bills nothing but must not
                # vanish from the failure totals: a dead fallback model is a
                # measurement, not an absence of one.
                ok=bool(usage.get("ok", True)),
                error=usage.get("error"),
            )

        return _record

    # -- aggregates ---------------------------------------------------------

    @property
    def n_calls(self) -> int:
        with self._lock:
            return len(self.calls)

    @property
    def n_failed_calls(self) -> int:
        with self._lock:
            return sum(1 for c in self.calls if not c.ok)

    @property
    def total_prompt_tokens(self) -> int:
        with self._lock:
            return sum(c.prompt_tokens for c in self.calls)

    @property
    def total_completion_tokens(self) -> int:
        with self._lock:
            return sum(c.completion_tokens for c in self.calls)

    @property
    def total_cost_cny(self) -> float:
        with self._lock:
            return sum(c.cost_cny for c in self.calls)

    @property
    def over_budget(self) -> bool:
        return self.budget_cny is not None and self.total_cost_cny > self.budget_cny

    def check_budget(self) -> None:
        """Raise :class:`BudgetExceeded` when the accumulated cost is over."""
        if self.over_budget:
            raise BudgetExceeded(
                f"budget exceeded: spent ¥{self.total_cost_cny:.4f} of "
                f"¥{self.budget_cny:.2f} after {self.n_calls} call(s); "
                "aborting the run"
            )

    def by_model(self) -> dict[str, dict]:
        with self._lock:
            calls = list(self.calls)
        out: dict[str, dict] = {}
        for c in calls:
            entry = out.setdefault(
                c.model,
                {
                    "calls": 0,
                    "failed_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_cny": 0.0,
                    "price": price_for(c.model, self.pricing).to_dict(),
                },
            )
            entry["calls"] += 1
            entry["failed_calls"] += 0 if c.ok else 1
            entry["prompt_tokens"] += c.prompt_tokens
            entry["completion_tokens"] += c.completion_tokens
            entry["total_tokens"] += c.total_tokens
            entry["cost_cny"] += c.cost_cny
        for entry in out.values():
            entry["cost_cny"] = round(entry["cost_cny"], 6)
        return dict(sorted(out.items()))

    def summary(self) -> dict:
        return {
            "n_calls": self.n_calls,
            "n_failed_calls": self.n_failed_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "total_cost_cny": round(self.total_cost_cny, 6),
            "budget_cny": self.budget_cny,
            "over_budget": self.over_budget,
            "by_model": self.by_model(),
            "pricing": {
                model: price.to_dict()
                for model, price in sorted(
                    (self.pricing or PRICING).items()
                )
            },
        }


# --- per-round aggregation -------------------------------------------------


#: Recorded field-level accuracy of the earlier real-API benchmark run, for the
#: comparison the task this page answers demands. That runner
#: (`scripts/run_benchmark.py --extractor dashscope`) scores a failed extraction
#: as an empty document, so it is only comparable to the *all-in* convention.
BASELINE_FIELD_ACCURACY = 0.8249
BASELINE_FIELD_ACCURACY_SOURCE = (
    "benchmark/results/real_qwen-vl-plus.json — run_benchmark.py --extractor "
    "dashscope, 30 invoices, 3 extraction failures, same batch"
)

#: The end-to-end run published *before* the JSON-repair work landed. Recorded
#: here for the same reason as :data:`BASELINE_FIELD_ACCURACY`: the raw artifact
#: is untracked regenerated output, so a number that has to survive belongs in
#: the source that prints it. Every value below was read out of that artifact
#: (still on disk at ``benchmark/results/e2e_eval_before_json_fix.json``).
PRE_FIX_BASELINE: dict[str, Any] = {
    "label": "before the JSON-repair fix",
    "generated_at": "2026-09-16T13:47:02",
    "source": (
        "benchmark/results/e2e_eval_before_json_fix.json — "
        "scripts/run_e2e_eval.py --rounds 3, same 30 invoices, "
        "commit 9eb6ef1 (the run published as the previous version of this page)"
    ),
    "attempts": 90,
    "extracted": 79,
    "extraction_failures": 11,
    "comparison": {
        "extracted_subset": {
            "precision": 0.6579,
            "recall": 0.8065,
            "f1": 0.7247,
            "tp": 25,
            "fp": 13,
            "fn": 6,
        },
        "full_batch": {
            "precision": 0.6579,
            "recall": 0.6944,
            "f1": 0.6757,
            "tp": 25,
            "fp": 13,
            "fn": 11,
        },
    },
    "field_accuracy": 0.9421,
    "field_accuracy_allin": 0.8272,
    "calls": 117,
    "cost_cny": 0.360523,
    "mean_invoice_seconds": 16.69,
    "invoices_per_minute": 11.3,
}


def _stats(values: Sequence[float]) -> dict:
    """Mean/min/max/range for one metric across rounds.

    ``range`` is ``None`` (and ``variance_measurable`` false) for a single
    round: one observation cannot express spread, and reporting 0.0 there
    would read as "no variance" rather than "not measured".
    """
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"values": [], "n": 0, "mean": None, "min": None, "max": None, "range": None}
    return {
        "values": [round(v, 4) for v in vals],
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "range": round(max(vals) - min(vals), 4) if len(vals) > 1 else None,
    }


def _prf(tp: int, fp: int, fn: int) -> dict:
    """Micro precision/recall/F1 — same rounding as ``audit_metrics._prf``."""
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    else:
        f1 = None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def micro_from_audit(audit: Mapping[str, Any]) -> dict:
    """Overall micro P/R/F1 summed over an ``evaluate_audit`` result's classes."""
    classes = (audit or {}).get("classes") or {}
    tp = sum(int(m.get("tp", 0)) for m in classes.values())
    fp = sum(int(m.get("fp", 0)) for m in classes.values())
    fn = sum(int(m.get("fn", 0)) for m in classes.values())
    return _prf(tp, fp, fn)


def derive_full_batch_audit(
    subset_audit: Mapping[str, Any],
    gt_files: Mapping[str, dict],
    *,
    all_names: Sequence[str],
    audited_names: Sequence[str],
    extra_labels: Mapping[str, Iterable[str]] | None = None,
) -> dict:
    """Fold invoices that could not be audited back into the micro counts.

    Documents whose extraction failed are dropped before the audit (that is
    what the shipped API does), so they contribute no findings — and no
    detected anomalies either. Scoring only the surviving subset would hide
    that loss, so every labelled anomaly on a dropped invoice is added as a
    false negative. Precision is unaffected: a dropped document cannot
    manufacture a finding.
    """
    audited = set(audited_names)
    missing: dict[str, list[str]] = {}
    for name in all_names:
        if name in audited:
            continue
        missing[name] = list((gt_files.get(name) or {}).get("anomalies") or [])
    for name, labels in (extra_labels or {}).items():
        bucket = missing.setdefault(name, [])
        for label in labels:
            if label not in bucket:
                bucket.append(label)

    classes: dict[str, dict] = {}
    for cls, metrics in ((subset_audit or {}).get("classes") or {}).items():
        extra = sorted(n for n, labels in missing.items() if cls in labels)
        fn_invoices = sorted(set(metrics.get("fn_invoices") or []) | set(extra))
        entry = dict(metrics)
        entry["fn"] = len(fn_invoices)
        entry["fn_invoices"] = fn_invoices
        entry["fn_from_unauditable"] = extra
        entry.update(_prf(entry["tp"], entry["fp"], entry["fn"]))
        classes[cls] = entry

    out = dict(subset_audit or {})
    out["classes"] = classes
    out["overall"] = micro_from_audit({"classes": classes})
    out["n_unauditable_invoices"] = len(missing)
    out["unauditable_invoices"] = sorted(missing)
    return out


def summarize_rounds(rounds: Sequence[Mapping[str, Any]]) -> dict:
    """Collapse repeated runs into mean/min/max/range per metric."""
    if not rounds:
        raise ValueError("no completed rounds — nothing to summarise")

    def _accuracy_blocks(key: str) -> tuple[list[float], dict[str, list[float]]]:
        overall: list[float] = []
        per: dict[str, list[float]] = defaultdict(list)
        for rnd in rounds:
            report = rnd.get(key) or {}
            value = (report.get("overall") or {}).get("accuracy")
            if value is not None:
                overall.append(value)
            for name, metrics in report.items():
                if name.startswith("_") or name == "overall":
                    continue
                per[name].append(metrics.get("accuracy"))
        return overall, per

    overall_vals, per_field = _accuracy_blocks("field_report")
    allin_vals, allin_per_field = _accuracy_blocks("field_report_allin")

    audit_micro: dict[str, dict] = {}
    for key in ("e2e", "gt", "e2e_full"):
        block = {}
        for metric in ("precision", "recall", "f1"):
            block[metric] = _stats(
                [
                    (micro_from_audit(rnd.get(audit_key) or {}) or {}).get(metric)
                    for rnd in rounds
                    for audit_key in {
                        "e2e": ("audit_e2e",),
                        "gt": ("audit_gt",),
                        "e2e_full": ("audit_e2e_full",),
                    }[key]
                ]
            )
        block["tp"] = _stats(
            [
                micro_from_audit(rnd.get(audit_key) or {})["tp"]
                for rnd in rounds
                for audit_key in {
                    "e2e": ("audit_e2e",),
                    "gt": ("audit_gt",),
                    "e2e_full": ("audit_e2e_full",),
                }[key]
            ]
        )
        audit_micro[key] = block

    failures = [len(rnd.get("extraction_failures") or []) for rnd in rounds]
    return {
        "n_rounds": len(rounds),
        "variance_measurable": len(rounds) > 1,
        "field_accuracy": {
            "overall": _stats(overall_vals),
            "per_field": {name: _stats(vals) for name, vals in sorted(per_field.items())},
        },
        # ``None`` (not 1.0, not 0.0) when the runner did not record the all-in
        # convention: the two are not interchangeable and must not be conflated.
        "field_accuracy_allin": (
            {
                "overall": _stats(allin_vals),
                "per_field": {
                    name: _stats(vals) for name, vals in sorted(allin_per_field.items())
                },
            }
            if allin_vals
            else None
        ),
        "baseline_field_accuracy": {
            "value": BASELINE_FIELD_ACCURACY,
            "source": BASELINE_FIELD_ACCURACY_SOURCE,
            "convention": "failed extraction counted as an empty document",
        },
        "audit_micro": audit_micro,
        "extraction_failures": {
            **_stats(failures),
            "total": sum(failures),
        },
    }


# --- divergence analysis ---------------------------------------------------

#: Fields each anomaly class's rule actually reads. Used to show *which*
#: extracted value changed when a label stops (or starts) being detected.
CLASS_FIELDS: dict[str, tuple[str, ...]] = {
    "duplicate_number": ("invoice_number",),
    "arithmetic_mismatch": (
        "amount_excluding_tax",
        "tax_amount",
        "amount_including_tax",
    ),
    "anomalous_tax_rate": (
        "items.tax_rate",
        "items.amount_excluding_tax",
        "items.tax_amount",
    ),
    "missing_seller_tax_id": ("seller.tax_id",),
    "self_dealing": ("buyer.name", "seller.name"),
    "qr_mismatch": (
        "qr_payload",
        "invoice_number",
        "amount_including_tax",
        "issue_date",
    ),
    "future_date": ("issue_date",),
}


def _get_path(doc: Any, path: str) -> Any:
    if doc is None:
        return None
    if path.startswith("items."):
        attr = path.split(".", 1)[1]
        return [getattr(item, attr, None) for item in (getattr(doc, "items", None) or [])]
    if "." in path:
        head, rest = path.split(".", 1)
        obj = getattr(doc, head, None)
        return getattr(obj, rest, None) if obj is not None else None
    return getattr(doc, path, None)


def driving_field_values(doc: Any, fields: Iterable[str]) -> dict:
    return {path: _get_path(doc, path) for path in fields}


def field_deltas(gt_doc: Any, pred_doc: Any, fields: Iterable[str]) -> list[dict]:
    """Per-field ``gt``/``pred``/``changed`` rows, in ``fields`` order."""
    out = []
    for path in fields:
        gt_value = _get_path(gt_doc, path)
        pred_value = _get_path(pred_doc, path)
        out.append(
            {
                "field": path,
                "gt": gt_value,
                "pred": pred_value,
                "changed": gt_value != pred_value,
            }
        )
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def consistency_verdict(
    cls: str, doc: Any, tolerance: float = 0.02
) -> bool | None:
    """Would this class's rule find the document consistent?

    ``True`` = the rule sees nothing wrong, ``False`` = the rule should fire,
    ``None`` = undeterminable from this document alone (missing fields, or a
    batch-scoped rule). This distinguishes "the anomaly was masked by a
    self-consistent misreading" from "the rule never looked".
    """
    if cls == "arithmetic_mismatch":
        excl = getattr(doc, "amount_excluding_tax", None)
        tax = getattr(doc, "tax_amount", None)
        incl = getattr(doc, "amount_including_tax", None)
        if excl is None or tax is None or incl is None:
            return None
        return abs(incl - (excl + tax)) <= tolerance

    if cls == "anomalous_tax_rate":
        items = getattr(doc, "items", None) or []
        if not items:
            return None
        from app.core.config import get_settings
        from app.services.audit.rules.tax_rate import _IMPLIED_RATE_TOLERANCE_PP

        allowed = set(get_settings().allowed_tax_rates)
        for item in items:
            rate = item.tax_rate
            if rate is None:
                return None
            if rate not in allowed:
                return False
            amount, tax = item.amount_excluding_tax, item.tax_amount
            if amount and tax:
                implied = round(tax / amount * 100.0, 2)
                if abs(implied - rate) > _IMPLIED_RATE_TOLERANCE_PP:
                    return False
        return True

    if cls == "future_date":
        issue = getattr(doc, "issue_date", None)
        if issue is None:
            return None
        return issue <= date.today() + timedelta(days=1)

    if cls == "self_dealing":
        buyer = (getattr(getattr(doc, "buyer", None), "name", "") or "").strip()
        seller = (getattr(getattr(doc, "seller", None), "name", "") or "").strip()
        if not buyer or not seller:
            return None
        return buyer != seller

    if cls == "missing_seller_tax_id":
        from app.core.uscc import TAX_ID_PATTERN, uscc_checksum_ok
        import re

        tax_id = (getattr(getattr(doc, "seller", None), "tax_id", None) or "").strip()
        if not tax_id:
            return False
        if not re.compile(TAX_ID_PATTERN).match(tax_id):
            return False
        if len(tax_id) == 18 and not uscc_checksum_ok(tax_id):
            return False
        return True

    if cls == "qr_mismatch":
        from app.services.extraction.qr_utils import parse_qr_payload

        payload = getattr(doc, "qr_payload", None)
        if not payload:
            return None
        parsed = parse_qr_payload(payload)
        if parsed is None:
            return False
        number = getattr(doc, "invoice_number", None)
        incl = getattr(doc, "amount_including_tax", None)
        issue = getattr(doc, "issue_date", None)
        if parsed.number and number and parsed.number != number:
            return False
        if parsed.amount and incl is not None and abs(parsed.amount - incl) > tolerance:
            return False
        if parsed.date and issue is not None and parsed.date != issue:
            return False
        return True

    # duplicate_number is batch-scoped: one document cannot answer it.
    return None


def _serialize_finding(finding: Any) -> dict:
    severity = _field(finding, "severity")
    if severity is not None and not isinstance(severity, str):
        severity = getattr(severity, "name", str(severity))
    return {
        "rule_id": _field(finding, "rule_id"),
        "field": _field(finding, "field"),
        "severity": severity,
        "message": _field(finding, "message"),
    }


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _attribute(findings: Sequence[Any], names: Sequence[str]) -> dict[str, list]:
    """Map each finding to the audited invoice(s) it refers to."""
    by_name: dict[str, list] = defaultdict(list)
    for finding in findings:
        index = _field(finding, "invoice_index")
        if isinstance(index, int) and 0 <= index < len(names):
            by_name[names[index]].append(finding)
    return by_name


def classify_divergences(
    gt_files: Mapping[str, dict],
    *,
    gt_audit: Mapping[str, Any],
    e2e_audit: Mapping[str, Any],
    names: Sequence[str],
    gt_docs: Sequence[Any],
    pred_docs: Sequence[Any],
    findings: Sequence[Any] = (),
    extraction_failures: Sequence[str] = (),
    tolerance: float = 0.02,
) -> dict:
    """Explain every end-to-end vs labelled-field difference, per invoice.

    ``masked`` entries are labelled anomalies the pipeline no longer reports;
    ``manufactured`` entries are findings on invoices the labels call clean;
    ``unauditable`` entries are labelled invoices that never reached the audit
    because their extraction failed.
    """
    names = list(names)
    gt_by_name = dict(zip(names, gt_docs))
    pred_by_name = dict(zip(names, pred_docs))
    failed = set(extraction_failures)
    findings_by_name = _attribute(findings, names)
    audited = set(names)
    extracted_numbers = {
        name: (
            getattr(pred_by_name.get(name), "invoice_number", None) or None
            if pred_by_name.get(name) is not None
            else None
        )
        for name in names
    }

    masked: list[dict] = []
    manufactured: list[dict] = []
    unauditable: dict[str, dict] = {}

    for cls, refs in sorted(ANOMALY_TO_RULES.items()):
        labelled = {
            name
            for name, entry in gt_files.items()
            if cls in ((entry or {}).get("anomalies") or [])
        }
        e2e_class = ((e2e_audit or {}).get("classes") or {}).get(cls) or {}
        gt_class = ((gt_audit or {}).get("classes") or {}).get(cls) or {}
        e2e_tp = set(e2e_class.get("tp_invoices") or [])
        e2e_fp = set(e2e_class.get("fp_invoices") or [])
        e2e_fn = set(e2e_class.get("fn_invoices") or [])
        gt_tp = set(gt_class.get("tp_invoices") or [])
        audited = set(names)

        # FP/FN invoices are the audit report's own verdict; the set arithmetic
        # on top of them keeps the classification correct even if a report is
        # missing one of the three lists.
        masked_names = e2e_fn | ((labelled & audited) - e2e_tp)
        manufactured_names = e2e_fp | (e2e_tp - labelled)

        for name in sorted(labelled - audited):
            entry = unauditable.setdefault(
                name,
                {
                    "invoice": name,
                    "classes": [],
                    "extraction_failed": name in failed,
                },
            )
            entry["classes"].append(cls)

        for name in sorted(masked_names):
            masked.append(
                _divergence(
                    cls, name, "masked", refs,
                    gt_files=gt_files, gt_by_name=gt_by_name,
                    pred_by_name=pred_by_name, findings_by_name=findings_by_name,
                    gt_tp=gt_tp, failed=failed, tolerance=tolerance,
                    labelled=labelled, audited=audited,
                    extracted_numbers=extracted_numbers,
                )
            )
        for name in sorted(manufactured_names):
            manufactured.append(
                _divergence(
                    cls, name, "manufactured", refs,
                    gt_files=gt_files, gt_by_name=gt_by_name,
                    pred_by_name=pred_by_name, findings_by_name=findings_by_name,
                    gt_tp=gt_tp, failed=failed, tolerance=tolerance,
                    labelled=labelled, audited=audited,
                    extracted_numbers=extracted_numbers,
                )
            )

    return {
        "masked": masked,
        "manufactured": manufactured,
        "unauditable": [unauditable[name] for name in sorted(unauditable)],
    }


def _partner_state(
    cls: str,
    name: str,
    gt_files: Mapping[str, dict],
    labelled: set[str],
    audited: set[str],
    extracted_numbers: Mapping[str, str | None],
) -> list[dict]:
    """For batch-scoped classes: the other invoices the rule needed.

    ``dup_invoice_number`` only fires when two invoices share a number, so a
    masking of that class can be caused by a *partner* invoice rather than by
    anything wrong with this one.
    """
    if cls != "duplicate_number":
        return []
    gt_number = (gt_files.get(name) or {}).get("invoice_number")
    own = extracted_numbers.get(name)
    state = []
    for other in sorted(labelled):
        if other == name:
            continue
        if (gt_files.get(other) or {}).get("invoice_number") != gt_number:
            continue
        other_number = extracted_numbers.get(other)
        state.append(
            {
                "invoice": other,
                "audited": other in audited,
                "gt_number": gt_number,
                "extracted_number": other_number,
                "same_extracted_number": (
                    other in audited and other_number is not None and other_number == own
                ),
            }
        )
    return state


def _divergence(
    cls: str,
    name: str,
    kind: str,
    refs: Sequence[Any],
    *,
    gt_files: Mapping[str, dict],
    gt_by_name: Mapping[str, Any],
    pred_by_name: Mapping[str, Any],
    findings_by_name: Mapping[str, Sequence[Any]],
    gt_tp: set[str],
    failed: set[str],
    tolerance: float,
    labelled: set[str] | None = None,
    audited: set[str] | None = None,
    extracted_numbers: Mapping[str, str | None] | None = None,
) -> dict:
    fields = CLASS_FIELDS.get(cls, ())
    gt_doc = gt_by_name.get(name)
    pred_doc = pred_by_name.get(name)
    deltas = [
        {**row, "gt": _jsonable(row["gt"]), "pred": _jsonable(row["pred"])}
        for row in field_deltas(gt_doc, pred_doc, fields)
    ] if (gt_doc is not None and pred_doc is not None) else []
    changed = [row["field"] for row in deltas if row["changed"]]
    extraction_failed = name in failed
    consistency = (
        consistency_verdict(cls, pred_doc, tolerance) if pred_doc is not None else None
    )
    partner_state = _partner_state(
        cls, name, gt_files, labelled or set(), audited or set(), extracted_numbers or {}
    )
    partner_lost = any(not p["audited"] or not p["same_extracted_number"] for p in partner_state)
    relevant = [
        _serialize_finding(f)
        for f in findings_by_name.get(name, [])
        if any(ref.matches(f) for ref in refs)
    ]

    if kind == "masked":
        if extraction_failed:
            mechanism = "masked_extraction_failed"
        elif partner_lost:
            mechanism = "masked_partner_lost"
        elif changed:
            mechanism = "masked_value_error"
        else:
            mechanism = "masked_unexplained"
    else:
        if extraction_failed:
            mechanism = "manufactured_extraction_failure"
        elif changed:
            mechanism = "manufactured_value_error"
        else:
            mechanism = "manufactured_unexplained"

    explained = bool(changed) or extraction_failed or partner_lost
    return {
        "class": cls,
        "invoice": name,
        "kind": kind,
        "mechanism": mechanism,
        "rule_refs": [str(ref) for ref in refs],
        "labels": list((gt_files.get(name) or {}).get("anomalies") or []),
        "gt_detected": name in gt_tp,
        "extraction_failed": extraction_failed,
        "changed_fields": changed,
        "deltas": deltas,
        "partner_state": partner_state,
        "internally_consistent": _jsonable(consistency),
        "explained_by_extraction": explained,
        "unexplained": kind == "masked" and not explained,
        "e2e_findings": relevant,
    }


# --- markdown --------------------------------------------------------------

_RECOMMENDATIONS = """\
## Recommendations

Ordered by the evidence above, not by effort. Each one names the measurement
that motivates it; none of them is a tolerance tweak, because the errors that
actually moved these metrics are one to three orders of magnitude larger than
the tolerance (¥50.00 and ¥2.00 against a ¥0.02 threshold), so widening it
would change nothing except the ability to detect real anomalies.

1. **Make the JSON failure mode survivable — done, and it was the highest-value
   change in this list.** Every extraction failure in the pre-fix run was the
   same defect (*malformed JSON in model response*), so the binding constraint
   was the decode path, not the audit rules. Two repairs removed all 11
   failures without touching a rule: closing an object the model forgot to
   close, and refusing to let the model's repeated placeholder skeleton
   overwrite a value it had already read correctly (the naive last-key-wins
   parse turned a read `金额: 70.3` into `金额: 0`). See the before/after
   section: micro F1 0.6757 → 0.8148, with the residual now pure OCR error.
   What remains of this item is the *re-extraction queue* for responses that
   are unusable even after repair — one call in this run.
2. **Re-extract before auditing; never score an unread document as clean.**
   An extraction failure is not a clean invoice and not a suspicious one — it
   is an unanswered question. The pipeline already refuses to call such a batch
   conclusive (`audit_conclusive=false`); the missing step is to route the file
   to a retry/second-read queue, and to keep its labels out of the "detected"
   column rather than counting them as satisfied.
3. **Widen the deterministic tax-id repair to the forbidden charset.** The
   `party_info` false positives here are all one mechanism: the model wrote a
   lookalike letter where a digit belonged (`Z` for `2`, `O` for `0`), and
   GB 32100-2015 excludes `I O Z S V` from the code alphabet. `repair_uscc`
   deliberately refuses to touch such a body, because it only rewrites check
   characters. A deterministic lookalike map (`O→0`, `I→1`, `Z→2`, `S→5`,
   `V→U`) followed by a checksum verification repaired **both observed cases
   exactly**, and the verification is what makes it safe: a repair is accepted
   only when the recomputed check character matches the printed one, so it can
   never invent an identifier. This removes a whole class of false alarms at
   zero risk of masking a real one.
4. **Cross-check arithmetic between independent channels.** `arithmetic_total`
   compares three numbers the model read from the same region, so a misreading
   that stays internally consistent is invisible to it — that is the masked
   case here, in every round. The invoice already carries an independent
   channel: the QR payload. It is decoded from the pixels, not read by the
   model, and in this run it caught exactly the error `arithmetic_total` lost
   (the manufactured `qr_mismatch` on the same invoice, same round). Prefer the
   cross-source rule where both exist, and use the same-source rule as the
   fallback.
5. **Give batch-scoped rules a degraded mode.** When one of two invoices
   sharing a number fails extraction, the survivor's number becomes unique and
   `dup_invoice_number` silently cannot fire — the surviving invoice is a
   double loss (its partner's labels are missed *and* its own). A rule that
   knows its batch was incomplete should report "duplicate check not performed"
   rather than nothing, so the gap is visible.
6. **Calibrate confidence into the audit rather than beside it.** The
   `low_confidence` rule exists but only warns. The honest move is to let it
   gate arithmetic and tax-rate findings: a rule that fires only because a
   low-confidence field was misread should downgrade to "needs review", which
   is what a human reviewer would do anyway. The repair work added a concrete
   case for this: the prompt tells the model to write `0` for a field it cannot
   read, so a *confidently unread* amount reaches the engine as a real zero and
   reads as an arithmetic mismatch. Nothing in the engine currently
   distinguishes "the invoice says 0" from "the model gave up on this field".
7. **Publish recall over the whole batch, not just the auditable subset.**
   Dropping unreadable documents before scoring converts an extraction failure
   into an exclusion, which is how a pipeline's real recall gets flattered.
   This page prints both; the habit is worth keeping everywhere.
"""


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str) and value == "":
        return "*(empty)*"
    return f"`{value}`"


def render_e2e_markdown(report: Mapping[str, Any]) -> str:
    """Render the evaluation report as the `docs/e2e-eval.md` page."""
    rounds = list(report.get("rounds") or [])
    comparison = report.get("comparison") or {}
    cost = report.get("cost") or {}
    latency = report.get("latency") or {}
    extrapolation = report.get("extrapolation") or {}
    aborted = report.get("aborted")

    lines = [
        "# End-to-end evaluation (real extraction → audit → score)",
        "",
        "> Generated by `python scripts/run_e2e_eval.py` — do not edit by hand.",
        "",
        "Every other evaluation page in this repository isolates one component.",
        "This one measures the pipeline a user actually gets: PDF → vision model",
        "→ normalized fields → audit rules → findings, scored against the",
        "injected labels in `benchmark/ground_truth.json`. The question it answers",
        "is the one an interviewer asks first — *does the audit still work when",
        "the OCR is wrong?*",
        "",
        "## Evaluation set",
        "",
        f"- **Invoices**: {report.get('n_invoices')} synthetic Chinese e-invoices "
        "(fully fabricated — see [data-compliance.md](data-compliance.md))",
        f"- **Extractor**: `{report.get('extractor')}` "
        + (
            "via the project's own `DashScopeExtractor` — prompt, DPI and "
            "first-attempt temperature unchanged; a response that could not be "
            "turned into an invoice gets one corrective re-read (see the "
            "before/after section)"
            if report.get("extractor") == "dashscope"
            else "(harness sanity run: no API calls, no cost — the numbers below "
            "verify the harness, not the model)"
        ),
        f"- **Models**: primary `{(report.get('models') or {}).get('primary')}`, "
        f"fallback `{(report.get('models') or {}).get('fallback')}`",
        f"- **Rounds requested / completed**: {report.get('rounds_requested')} / "
        f"{report.get('rounds_completed')} (same batch, same parameters — the",
        "  spread below is model non-determinism, not a parameter change)",
        f"- **Numeric tolerance**: ¥{report.get('tolerance')} (unchanged from the",
        "  field benchmark; widening it would hide exactly the OCR errors this",
        "  page exists to measure)",
        f"- **Generated**: {report.get('generated_at')}",
        "",
    ]

    if aborted:
        lines += [
            "> **RUN ABORTED**: " + str(aborted.get("reason", "unknown reason")),
            "",
        ]
    if not rounds:
        lines += [
            "**No round completed.** Every metric on this page is *not measured* — "
            "an aborted run has no numbers, and reporting 0 (or 1.0) here would be "
            "a fabrication. The reason is recorded above.",
            "",
        ]

    lines += [
        "## Cost and latency",
        "",
        "Token counts are the real `usage` returned by the API for every call,",
        "including calls whose response was later discarded as unparseable.",
        "Prices are list prices, quoted with their source in the JSON artifact —",
        "they are inputs to the cost model, not measurements.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| API calls | {cost.get('n_calls', '—')} |",
        f"| Input tokens | {cost.get('total_prompt_tokens', '—')} |",
        f"| Output tokens | {cost.get('total_completion_tokens', '—')} |",
        f"| **Total cost (list price)** | **¥{_fmt(cost.get('total_cost_cny'), 4)}** |",
        f"| Budget set | ¥{cost.get('budget_cny', '—')} |",
        f"| Wall clock (all rounds) | {_fmt(latency.get('total_seconds'), 1)}s |",
        f"| Mean single-invoice latency | {_fmt(latency.get('mean_invoice_seconds'), 1)}s |",
        f"| Effective throughput (workers="
        f"{latency.get('workers', '—')}) | {_fmt(latency.get('invoices_per_minute'), 2)} invoices/min |",
        "",
        "Whether an individual response was *usable* is not separately metered: "
        "the usage hook fires as soon as the response arrives, before JSON "
        "parsing, which is what makes a billed-but-discarded call visible in the "
        "token totals at all. The measured equivalent is the invoice-level "
        "Extraction failures count in each round below.",
        "",
    ]

    by_model = cost.get("by_model") or {}
    if by_model:
        lines += [
            "Per model:",
            "",
            "| Model | calls | failed | input tokens | output tokens | cost (¥) |",
            "|---|---|---|---|---|---|",
        ]
        for model, entry in by_model.items():
            lines.append(
                f"| `{model}` | {entry.get('calls')} | {entry.get('failed_calls')} | "
                f"{entry.get('prompt_tokens')} | {entry.get('completion_tokens')} | "
                f"{_fmt(entry.get('cost_cny'), 6)} |"
            )
        lines.append("")

    if extrapolation:
        lines += [
            "**Extrapolation to "
            f"{extrapolation.get('invoices'):,} invoices** (linear in the measured "
            "per-invoice cost and throughput — see the limitations):",
            "",
            f"- Cost: **¥{extrapolation.get('cost_cny', 0):,.0f}**",
            f"- Wall clock at {extrapolation.get('workers')} workers: "
            f"**{_fmt(extrapolation.get('wall_clock_hours'), 1)} hours**",
            f"- Human review queue: at the measured extraction-failure rate "
            f"({_fmt(extrapolation.get('failure_rate'), 4)}), "
            f"**{extrapolation.get('failed_invoices', 0):,.0f} documents** still "
            "need a human or a second model pass",
            "",
        ]

    if report.get("extractor") == "dashscope":
        lines += [_before_after(report), ""]

    lines += [
        "## Per-round raw results",
        "",
        "Nothing is averaged away: each round is reported exactly as measured.",
        "",
    ]
    for rnd in rounds:
        failures = rnd.get("extraction_failures") or []
        lines += [
            f"### Round {rnd.get('round')}",
            "",
            f"- Wall clock: {_fmt(rnd.get('elapsed_s'), 1)}s",
            f"- Invoices extracted: {len(rnd.get('extracted_names') or [])} / "
            f"{report.get('n_invoices')} (failures: {len(failures)})",
            f"- Audit micro (extracted input): "
            f"{_micro_line(rnd.get('audit_e2e'))}",
            f"- Audit micro (labelled input): "
            f"{_micro_line(rnd.get('audit_gt'))}",
            f"- Audit micro (extracted input, failures counted as missed): "
            f"{_micro_line(rnd.get('audit_e2e_full'))}",
            "",
        ]
        if failures:
            lines += [
                "Extraction failures (recorded verbatim):",
                "",
                "| File | Error |",
                "|---|---|",
            ]
            for failure in failures:
                message = str(failure.get("error", "")).replace("|", "\\|")
                lines.append(f"| `{failure.get('file')}` | {message[:300]} |")
            lines.append("")

        unattributed = (rnd.get("audit_e2e") or {}).get("unattributed_findings") or []
        lines += [
            f"- Unattributed findings (could not be resolved to an invoice): "
            f"**{len(unattributed)}**",
            "",
        ]
        if unattributed:
            lines += [
                "A finding the harness cannot resolve to a file is credited to "
                "nobody: it can be neither a true nor a false positive. That "
                "under-counts both the detections and the false alarms, so the "
                "list is published rather than dropped:",
                "",
                "| rule_id | invoice_index | invoice_number |",
                "|---|---|---|",
            ]
            for item in unattributed:
                lines.append(
                    f"| `{item.get('rule_id')}` | {_cell(item.get('invoice_index'))} | "
                    f"{_cell(item.get('invoice_number'))} |"
                )
            lines.append("")

        field_report = rnd.get("field_report") or {}
        allin_report = rnd.get("field_report_allin") or {}
        subset_acc = ((field_report.get("overall") or {}).get("accuracy"))
        allin_acc = ((allin_report.get("overall") or {}).get("accuracy"))
        lines.append(
            f"- Field accuracy, auditable subset (failed files excluded): "
            f"{_fmt(subset_acc)} "
            f"({(field_report.get('overall') or {}).get('correct', '—')}/"
            f"{(field_report.get('overall') or {}).get('compared', '—')})"
        )
        if allin_acc is None:
            lines.append(
                "- Field accuracy, failed extractions counted as empty documents: "
                "**not measured** (the runner did not record this convention), so "
                f"no comparison with the recorded {BASELINE_FIELD_ACCURACY} "
                "baseline is possible for this round"
            )
        else:
            delta = (allin_acc - BASELINE_FIELD_ACCURACY) * 100
            lines.append(
                "- Field accuracy, failures counted as empty documents: "
                f"**{_fmt(allin_acc)}** — the convention of the recorded "
                f"{BASELINE_FIELD_ACCURACY} baseline "
                f"(`{BASELINE_FIELD_ACCURACY_SOURCE}`), i.e. "
                f"{delta:+.2f} pp against it"
            )
        lines.append("")

        if field_report:
            lines += [
                "Field accuracy (auditable subset):",
                "",
                "| Field | accuracy | correct / compared | mean confidence |",
                "|---|---|---|---|",
            ]
            for name, metrics in field_report.items():
                if name.startswith("_") or name == "overall":
                    continue
                lines.append(
                    f"| `{name}` | {_fmt(metrics.get('accuracy'))} | "
                    f"{metrics.get('correct')}/{metrics.get('compared')} | "
                    f"{_fmt(metrics.get('avg_confidence'))} |"
                )
            lines.append("")

        unmapped = (rnd.get("audit_e2e") or {}).get("unmapped_rule_findings") or {}
        if unmapped:
            lines += [
                "Findings from rules that back no anomaly class — reported, not "
                "counted as false positives: "
                + ", ".join(f"`{k}` x {v}" for k, v in unmapped.items()),
                "",
            ]

        rows = []
        for entry in rnd.get("per_invoice") or []:
            for field_name, comp in (entry.get("fields") or {}).items():
                if comp.get("compared") and not comp.get("match"):
                    rows.append(
                        f"| `{entry.get('file')}` | `{field_name}` | "
                        f"{_cell(comp.get('gt'))} | {_cell(comp.get('pred'))} |"
                    )
        lines += [
            f"Per-field failures ({len(rows)} rows — every wrong field, not the "
            "average over them):",
            "",
            "| File | Field | Ground truth | Extracted |",
            "|---|---|---|---|",
            *rows,
            "",
        ]

    summary = report.get("summary") or {}
    if summary.get("n_rounds"):
        lines += ["### Across rounds", ""]
        overall = (summary.get("field_accuracy") or {}).get("overall") or {}
        allin = (summary.get("field_accuracy_allin") or {}).get("overall") or {}
        lines += [
            f"- Field-level accuracy, auditable subset: mean "
            f"{_fmt(overall.get('mean'))}, range {_fmt(overall.get('range'))} "
            f"(values {overall.get('values')})",
        ]
        if summary.get("field_accuracy_allin"):
            lines.append(
                f"- Field-level accuracy, failures as empty documents: mean "
                f"{_fmt(allin.get('mean'))}, range {_fmt(allin.get('range'))} "
                f"(values {allin.get('values')}) — the convention comparable to "
                f"the recorded {BASELINE_FIELD_ACCURACY} baseline"
            )
        lines += [
            f"- Variance measurable: **{summary.get('variance_measurable')}**"
            + (
                ""
                if summary.get("variance_measurable")
                else " — a single round cannot express spread; the range is "
                "reported as unmeasurable rather than as 0"
            ),
            "",
        ]
        lines += [
            "| Metric | labelled input (mean / range) | extracted input (mean / range) | "
            "extracted, failures as misses (mean / range) |",
            "|---|---|---|---|",
        ]
        micro = summary.get("audit_micro") or {}
        for metric in ("precision", "recall", "f1"):
            cells = []
            for key in ("gt", "e2e", "e2e_full"):
                block = (micro.get(key) or {}).get(metric) or {}
                cells.append(
                    f"{_fmt(block.get('mean'))} / {_fmt(block.get('range'))}"
                )
            lines.append(f"| micro {metric} | " + " | ".join(cells) + " |")
        lines.append("")

    lines += [
        "## End-to-end vs labelled fields",
        "",
        "The same engine, the same labels, two inputs. This is the measurement",
        "the `docs/audit-eval.md` limitations paragraph used to defer — that page",
        "now reports it and links back here.",
        "",
        "| Input to the audit engine | micro P | micro R | micro F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, label in (
        ("labelled", "Labelled field values (`docs/audit-eval.md`)"),
        ("extracted_subset", "Real extraction output, auditable subset"),
        ("full_batch", "Real extraction output, failed extractions counted as missed"),
    ):
        entry = comparison.get(key) or {}
        lines.append(
            f"| {label} | {_fmt(entry.get('precision'))} | {_fmt(entry.get('recall'))} | "
            f"{_fmt(entry.get('f1'))} | {entry.get('tp', '—')} | {entry.get('fp', '—')} | "
            f"{entry.get('fn', '—')} |"
        )
    lines.append("")

    lines += ["## Failure modes", ""]
    any_divergence = False
    for rnd in rounds:
        divergences = rnd.get("divergences") or {}
        masked = divergences.get("masked") or []
        manufactured = divergences.get("manufactured") or []
        unauditable = divergences.get("unauditable") or []
        if not (masked or manufactured or unauditable):
            continue
        any_divergence = True
        lines += [
            f"### Round {rnd.get('round')}",
            "",
            f"- Masked (labelled anomaly no longer reported): **{len(masked)}**",
            f"- Manufactured (finding on an invoice the labels call clean): "
            f"**{len(manufactured)}**",
            f"- Not auditable (labelled invoice whose extraction failed): "
            f"**{len(unauditable)}**",
            "",
        ]
        for kind, entries, blurb in (
            (
                "Masked",
                masked,
                "The label was injected, the rule fired on labelled fields, and the "
                "misread value removed the signal.",
            ),
            (
                "Manufactured",
                manufactured,
                "The labels call this invoice clean; a misread value made a rule fire.",
            ),
        ):
            for item in entries:
                lines += [
                    f"**{kind} — `{item.get('invoice')}` / `{item.get('class')}`**",
                    "",
                    f"- Mechanism: `{item.get('mechanism')}`",
                    f"- Rule(s): {', '.join(f'`{r}`' for r in item.get('rule_refs') or [])}",
                    f"- Detected on labelled fields: `{item.get('gt_detected')}`",
                    f"- Driving fields that changed: "
                    + (
                        ", ".join(f"`{f}`" for f in item.get("changed_fields") or [])
                        or "*(none)*"
                    ),
                    f"- Document self-consistent after extraction: "
                    f"`{item.get('internally_consistent')}` — `True` means the rule "
                    "sees a coherent document and cannot fire",
                    "",
                    "| Driving field | labelled | extracted |",
                    "|---|---|---|",
                ]
                for row in item.get("deltas") or []:
                    marker = " **←**" if row.get("changed") else ""
                    lines.append(
                        f"| `{row.get('field')}` | {_cell(row.get('gt'))}{marker} | "
                        f"{_cell(row.get('pred'))} |"
                    )
                lines.append("")
                if item.get("partner_state"):
                    lines += [
                        "The rule is batch-scoped, so the other invoices sharing "
                        "this ground-truth number matter too:",
                        "",
                        "| Partner invoice | audited | ground-truth number | "
                        "extracted number | still shares the number |",
                        "|---|---|---|---|---|",
                    ]
                    for partner in item["partner_state"]:
                        lines.append(
                            f"| `{partner.get('invoice')}` | `{partner.get('audited')}` | "
                            f"{_cell(partner.get('gt_number'))} | "
                            f"{_cell(partner.get('extracted_number'))} | "
                            f"`{partner.get('same_extracted_number')}` |"
                        )
                    lines.append("")
                if item.get("e2e_findings"):
                    lines += [
                        "Findings the engine did emit for this invoice:",
                        "",
                    ]
                    for finding in item["e2e_findings"]:
                        lines.append(
                            f"- `{finding.get('rule_id')}` "
                            f"(`{finding.get('field')}`, {finding.get('severity')}): "
                            f"{finding.get('message')}"
                        )
                    lines.append("")
                if item.get("unexplained"):
                    lines += [
                        "> **Unexplained.** No driving field changed, yet the label "
                        "stopped being detected — that is not an OCR effect and "
                        "needs investigating in the harness itself.",
                        "",
                    ]
        for item in unauditable:
            lines += [
                f"**Unauditable — `{item.get('invoice')}`** "
                f"(classes: {', '.join(f'`{c}`' for c in item.get('classes') or [])})",
                "",
                "Extraction failed, so the document never reached the audit engine "
                "and its labels can only be missed. These are counted as false "
                "negatives in the full-batch row above.",
                "",
            ]
    if not any_divergence:
        lines += [
            "No divergence between the labelled-field run and the end-to-end run "
            "in any completed round: extraction errors changed no field that any "
            "mapped rule reads.",
            "",
        ]

    lines += [_RECOMMENDATIONS, _limitations(report)]
    return "\n".join(lines) + "\n"


def _micro_line(audit: Mapping[str, Any] | None) -> str:
    micro = micro_from_audit(audit or {})
    return (
        f"P={_fmt(micro['precision'])} R={_fmt(micro['recall'])} "
        f"F1={_fmt(micro['f1'])} (tp={micro['tp']} fp={micro['fp']} fn={micro['fn']})"
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _delta(
    before: float | None, after: float | None, *, digits: int = 4, points: bool = False
) -> str:
    """Signed change, in percentage points for rates and units otherwise."""
    if before is None or after is None:
        return "—"
    if isinstance(before, int) and isinstance(after, int):
        return f"{after - before:+d}"
    diff = after - before
    return f"{diff * 100:+.2f} pp" if points else f"{diff:+.{digits}f}"


def _before_after(report: Mapping[str, Any]) -> str:
    """The change against the run published before the JSON repair landed.

    A before/after claim that cannot be checked is marketing, so both sides of
    every row come from a recorded artifact: the *before* numbers from
    :data:`PRE_FIX_BASELINE` (raw artifact untracked, path recorded there) and
    the *after* numbers from the report being rendered.
    """
    base = PRE_FIX_BASELINE
    totals = report.get("totals") or {}
    comparison = report.get("comparison") or {}
    summary = report.get("summary") or {}
    cost = report.get("cost") or {}
    latency = report.get("latency") or {}

    attempts = totals.get("attempts")
    failures = totals.get("extraction_failures")
    after_rate = (failures / attempts) if attempts else None
    base_rate = base["extraction_failures"] / base["attempts"]
    after_subset = comparison.get("extracted_subset") or {}
    after_full = comparison.get("full_batch") or {}
    base_subset = base["comparison"]["extracted_subset"]
    base_full = base["comparison"]["full_batch"]
    after_acc = ((summary.get("field_accuracy") or {}).get("overall") or {}).get("mean")
    after_allin = ((summary.get("field_accuracy_allin") or {}).get("overall") or {}).get(
        "mean"
    )

    def read(before: int, total: int) -> str:
        return f"{before}/{total}" if total else "—"

    rows = [
        (
            "**Extraction failure rate**",
            f"**{_pct(base_rate)}** ({read(base['extraction_failures'], base['attempts'])})",
            f"**{_pct(after_rate)}** ({read(failures or 0, attempts or 0)})",
            f"{_delta(base_rate, after_rate, points=True)}",
        ),
        (
            "Invoices read",
            read(base["extracted"], base["attempts"]),
            read(totals.get("extracted") or 0, attempts or 0),
            _delta(base["extracted"], totals.get("extracted")),
        ),
        (
            "micro F1 — auditable subset",
            _fmt(base_subset["f1"]),
            _fmt(after_subset.get("f1")),
            _delta(base_subset["f1"], after_subset.get("f1")),
        ),
        (
            "micro F1 — failures counted as missed",
            _fmt(base_full["f1"]),
            _fmt(after_full.get("f1")),
            _delta(base_full["f1"], after_full.get("f1")),
        ),
        (
            "micro precision — failures counted as missed",
            _fmt(base_full["precision"]),
            _fmt(after_full.get("precision")),
            _delta(base_full["precision"], after_full.get("precision")),
        ),
        (
            "micro recall — failures counted as missed",
            _fmt(base_full["recall"]),
            _fmt(after_full.get("recall")),
            _delta(base_full["recall"], after_full.get("recall")),
        ),
        (
            "Field accuracy — auditable subset (mean)",
            _fmt(base["field_accuracy"]),
            _fmt(after_acc),
            _delta(base["field_accuracy"], after_acc),
        ),
        (
            "Field accuracy — failures as empty documents (mean)",
            _fmt(base["field_accuracy_allin"]),
            _fmt(after_allin),
            _delta(base["field_accuracy_allin"], after_allin),
        ),
        (
            "API calls",
            str(base["calls"]),
            str(cost.get("n_calls", "—")),
            _delta(base["calls"], cost.get("n_calls"), digits=0),
        ),
        (
            "Cost, list price (¥)",
            _fmt(base["cost_cny"], 4),
            _fmt(cost.get("total_cost_cny"), 4),
            _delta(base["cost_cny"], cost.get("total_cost_cny")),
        ),
        (
            "Mean invoice latency (s)",
            _fmt(base["mean_invoice_seconds"], 2),
            _fmt(latency.get("mean_invoice_seconds"), 2),
            _delta(base["mean_invoice_seconds"], latency.get("mean_invoice_seconds"), digits=2),
        ),
    ]

    lines = [
        "## Before / after the JSON-repair fix",
        "",
        "The previous version of this page measured the extraction-failure rate as "
        f"**{_pct(base_rate)}** ({base['extraction_failures']}/"
        f"{base['attempts']} attempts) and every single failure had the same cause. "
        "The repair work targets that cause; the numbers below say whether it worked, "
        "and the *before* column is a recorded run rather than a recollection.",
        "",
        f"- Baseline: `{base['source']}`",
        f"  (generated {base['generated_at']})",
        "",
        "| Metric | before | after | change |",
        "|---|---|---|---|",
    ]
    lines += [f"| {name} | {before} | {after} | {change} |" for name, before, after, change in rows]
    lines.append("")
    if after_acc is not None and after_allin is not None:
        lines += [
            "Two of those rows have to be read together. *Field accuracy — "
            "auditable subset* barely moves, but its population is not the same "
            "one: the baseline excluded the 3–4 documents per round it could not "
            "read, while this run excludes none — so the previously unreadable "
            "documents scored at roughly the batch average. The comparable row is "
            "the one below it, the whole batch with failures counted as misses, "
            f"which moved **{_delta(base['field_accuracy_allin'], after_allin, points=True)}**.",
            "",
        ]

    repairs = totals.get("json_repairs") or {}
    if repairs:
        lines += [
            "Responses the repair ladder recovered (recorded per document, so the "
            "recovery path is measured rather than assumed): "
            + ", ".join(f"`{name}` × {count}" for name, count in sorted(repairs.items())),
            "",
        ]
    else:
        lines += [
            "Responses the repair ladder recovered: **none** — no document needed a "
            "framing repair in this run.",
            "",
        ]
    if totals.get("fallback_reads"):
        lines += [
            f"Documents the fallback model had to read: **{totals['fallback_reads']}**",
            "",
        ]

    residual = [
        failure.get("file")
        for rnd in report.get("rounds") or []
        for failure in rnd.get("extraction_failures") or []
    ]
    if residual:
        lines += [
            f"Residual extraction failures in this run (the *after* column): "
            f"**{len(residual)}** — "
            + ", ".join(f"`{name}`" for name in residual)
            + ". They are counted as misses in the full-batch row above, not "
            "excluded; the per-round tables below name the reason for each.",
            "",
        ]
    else:
        lines += [
            "Residual extraction failures in this run (the *after* column): "
            "**none** — every attempt in this run produced an auditable document.",
            "",
        ]
    return "\n".join(lines)


def _limitations(report: Mapping[str, Any]) -> str:
    n_rounds = report.get("rounds_completed")
    return f"""\
## Honest limitations

Read these before quoting any number above.

- **{report.get('n_invoices')} synthetic invoices, {n_rounds} round(s).** The
  sample is small and machine-printed. A per-class figure here can rest on a
  single example; treat the failure *instances* as the finding and the
  aggregates as indicative.
- **Not a production distribution.** Real batches carry skewed tax rates,
  red-ink and voided invoices, multi-page scans and phone photos. Nothing here
  measures those, so real-world precision should be expected to be worse.
- **No working fallback model.** The configured fallback returned
  `403 insufficient_quota` throughout these runs, so every primary-model
  failure became a hard extraction failure rather than being absorbed. The
  extraction-failure rate above therefore describes the primary model *plus* a
  fallback that could not help; a deployment with a functioning second model —
  or a repair loop around the decode — should extract more documents, which
  raises recall without touching a single audit rule. Do not read the recall
  figures as the ceiling of the rules. (The pipeline now reports such a model as
  *unavailable* rather than as an attempt that read the document, and accounts
  for the refused call in the cost table, so this limitation is visible in the
  artifact instead of inferred from an error string.)
- **A repair can recover framing, not content.** The JSON repair closes a
  bracket the model forgot and drops the unterminated tail it was cut off in; it
  never supplies a value. A response that was truncated *before* it emitted the
  fields cannot be recovered, and a document recovered from a partial payload
  scores its missing fields as wrong (`compare_documents` counts an absent
  prediction as a miss), so the repair can raise the number of auditable
  documents but cannot flatter their accuracy.
- **One endpoint, one account.** The runs went through the OpenAI-compatible
  endpoint configured in the environment. A different deployment, region or
  model snapshot can produce different extraction errors; the model ids are
  recorded in the JSON artifact so a re-run can be compared.
- **Cost is list price, not an invoice.** Token counts are measured; the CNY
  figures apply published list prices to them. The account used here was on a
  free-tier quota for part of the run, so a ¥0 bill would be a billing
  artefact, not a measurement — see the JSON artifact for per-model token
  totals if you want to apply your own prices.
- **Latency depends on the network and on concurrency.** Wall clock was
  measured with the worker count recorded above; single-invoice latency is the
  per-call wall time, so the two answer different questions (how fast one
  invoice is vs how many per hour a batch gets through).
- **Extrapolation is linear by construction.** Cost and time scale with token
  volume and per-call latency; the extrapolation above assumes both hold at
  100k invoices. It does not model rate limits, quota exhaustion, the retry
  behaviour of a model that starts failing more often, or the human cost of the
  failures it leaves behind.
- **The labels are the generator's, not an auditor's.** The ground truth
  asserts which invoices were perturbed; it does not certify the remaining ones
  as clean, so any finding on an unlabelled invoice counts as a false positive
  here — including a finding that a human auditor would agree with.
- **Rounds are repeats, not a held-out split.** Repeating the same batch
  measures variance; it says nothing about generalisation.
"""
