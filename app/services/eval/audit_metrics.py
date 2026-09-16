"""Audit-engine evaluation: anomaly labels → rule findings → precision/recall.

The field-level benchmark (``app/services/eval/metrics.py``) answers *"did we
read the invoice correctly?"*. This module answers the question the audit
engine actually exists for: *"did we flag the right invoices?"*

Ground truth
------------
``benchmark/ground_truth.json`` annotates every synthetic invoice with a list
of injected ``anomalies`` (plain strings). ``scripts/generate_synthetic_invoices.py``
injects them deterministically by index — see :data:`ANOMALY_TO_RULES` for the
seven classes.

Evaluation unit
---------------
One *positive* is a ``(invoice, anomaly class)`` pair. A class is predicted
positive for an invoice when a mapped rule emitted a finding attributed to
that invoice (and, for rules that back several classes, matching the class's
field selector). This is deliberately **not** finding-count based: a rule that
emits three findings for one bad invoice does not earn three true positives.

Failure modes are loud
----------------------
A missing anomaly class, an unknown/typo'd rule id, an empty ground truth or a
ground truth with no anomalies at all all raise :class:`AuditEvalError`. An
evaluation harness that quietly returns 0 (or a flattering 1.0) when the
mapping is broken is worse than no harness — this is the false-negative
failure mode the audit product must never ship.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.core.config import find_repo_root
from app.models.invoice import InvoiceDocument

SCHEMA_VERSION = 1

DEFAULT_GROUND_TRUTH = "benchmark/ground_truth.json"


class AuditEvalError(ValueError):
    """Raised when the evaluation setup itself is wrong (never swallowed)."""


@dataclass(frozen=True)
class RuleRef:
    """A rule (optionally restricted to some ``field`` values) that should fire.

    ``fields`` exists because one rule can back several anomaly classes:
    ``party_info`` reports both "seller tax id is missing"
    (``field="seller.tax_id"``) and "buyer == seller" (``field="buyer.name"``).
    Without the selector, each class would score the other class's findings as
    false positives.
    """

    rule_id: str
    fields: tuple[str, ...] | None = None

    def __str__(self) -> str:  # readable + stable serialisation
        if not self.fields:
            return self.rule_id
        return f"{self.rule_id}@{'|'.join(self.fields)}"

    def matches(self, finding: Any) -> bool:
        if _field(finding, "rule_id") != self.rule_id:
            return False
        if not self.fields:
            return True
        return _field(finding, "field") in self.fields


#: The seven injected anomaly classes and the rule(s) responsible for them.
ANOMALY_TO_RULES: dict[str, tuple[RuleRef, ...]] = {
    "duplicate_number": (RuleRef("dup_invoice_number"),),
    "arithmetic_mismatch": (RuleRef("arithmetic_total"),),
    "anomalous_tax_rate": (RuleRef("tax_rate"),),
    "missing_seller_tax_id": (RuleRef("party_info", ("seller.tax_id",)),),
    "self_dealing": (RuleRef("party_info", ("buyer.name",)),),
    "qr_mismatch": (RuleRef("qr_crosscheck"),),
    "future_date": (RuleRef("invoice_date"),),
}


# --- small helpers ---------------------------------------------------------


def _field(finding: Any, name: str) -> Any:
    """Read ``name`` from an :class:`AuditFinding` or a plain dict."""
    if isinstance(finding, Mapping):
        return finding.get(name)
    return getattr(finding, name, None)


def _normalize_refs(refs: Iterable[Any]) -> tuple[RuleRef, ...]:
    out: list[RuleRef] = []
    for ref in refs:
        if isinstance(ref, RuleRef):
            out.append(ref)
        elif isinstance(ref, str):
            out.append(RuleRef(ref))
        else:  # pragma: no cover - defensive: caller passed something odd
            raise AuditEvalError(f"unsupported rule reference: {ref!r}")
    return tuple(out)


def _normalized_mapping(
    mapping: Mapping[str, Iterable[Any]] | None,
) -> dict[str, tuple[RuleRef, ...]]:
    source = ANOMALY_TO_RULES if mapping is None else mapping
    return {cls: _normalize_refs(refs) for cls, refs in source.items()}


def mapping_as_dict(mapping: Mapping[str, Iterable[Any]] | None = None) -> dict:
    """Serialisable view of the mapping (``{"class": ["rule@field", ...]}``)."""
    return {
        cls: [str(ref) for ref in refs]
        for cls, refs in _normalized_mapping(mapping).items()
    }


def mapped_rule_ids(mapping: Mapping[str, Iterable[Any]] | None = None) -> set[str]:
    return {
        ref.rule_id
        for refs in _normalized_mapping(mapping).values()
        for ref in refs
    }


def load_ground_truth(path: str | Path | None = None) -> dict:
    """Load ``ground_truth.json`` and return its ``files`` mapping."""
    gt_path = Path(path) if path else find_repo_root() / DEFAULT_GROUND_TRUTH
    data = json.loads(gt_path.read_text(encoding="utf-8"))
    files = data.get("files")
    if not isinstance(files, dict):
        raise AuditEvalError(f"{gt_path} has no usable 'files' mapping")
    return files


def ground_truth_classes(gt_files: Mapping[str, dict]) -> set[str]:
    return {a for entry in gt_files.values() for a in (entry.get("anomalies") or [])}


# --- validation ------------------------------------------------------------


def validate_mapping(
    gt_files: Mapping[str, dict],
    mapping: Mapping[str, Iterable[Any]] | None = None,
    registered_rule_ids: Iterable[str] | None = None,
) -> dict[str, tuple[RuleRef, ...]]:
    """Return the mapping, or raise :class:`AuditEvalError` explaining why not."""
    if not gt_files:
        raise AuditEvalError(
            "ground truth is empty — an evaluation over zero invoices is "
            "meaningless, refusing to report metrics"
        )

    normalized = _normalized_mapping(mapping)
    present = ground_truth_classes(gt_files)
    if not present:
        raise AuditEvalError(
            "ground truth contains no anomalies — recall is undefined, "
            "refusing to report metrics"
        )

    missing = sorted(present - set(normalized))
    if missing:
        raise AuditEvalError(
            "anomaly class(es) present in the ground truth but absent from the "
            f"mapping: {missing}. Add an explicit rule mapping; do not let the "
            "class score 0 silently."
        )

    if registered_rule_ids is None:
        from app.services.audit.base import get_registered_rules

        registered = set(get_registered_rules())
    else:
        registered = set(registered_rule_ids)

    unknown: list[str] = []
    for cls in sorted(present):
        for ref in normalized[cls]:
            if ref.rule_id not in registered:
                unknown.append(f"{cls} -> {ref.rule_id}")
            if ref.fields is not None and not ref.fields:
                unknown.append(f"{cls} -> {ref.rule_id} has an empty field selector")
    if unknown:
        raise AuditEvalError(
            "mapping references rule ids that are not registered (typo?): "
            f"{sorted(set(unknown))}; registered rules: {sorted(registered)}"
        )
    return normalized


# --- attribution -----------------------------------------------------------


def attribute_finding(
    finding: Any, names: Sequence[str], numbers: Mapping[str, str | None]
) -> list[str]:
    """Resolve a finding to the ground-truth file name(s) it refers to.

    Rules set ``invoice_index`` (position in the batch), ``invoice_number``, or
    both. Batch-level rules such as ``dup_invoice_number`` set no index but name
    the duplicated number, which legitimately covers several invoices.
    """
    index = _field(finding, "invoice_index")
    if isinstance(index, int) and 0 <= index < len(names):
        return [names[index]]

    number = _field(finding, "invoice_number")
    if number:
        hits = [n for n in names if numbers.get(n) == number]
        if hits:
            return hits
    return []


# --- metrics ---------------------------------------------------------------


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    else:
        f1 = None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def evaluate_audit(
    gt_files: Mapping[str, dict],
    findings: Sequence[Any],
    *,
    names: Sequence[str] | None = None,
    mapping: Mapping[str, Iterable[Any]] | None = None,
    registered_rule_ids: Iterable[str] | None = None,
) -> dict:
    """Score ``findings`` against the anomaly labels in ``gt_files``.

    ``names`` is the batch order the findings were produced with (defaults to
    the sorted ground-truth keys, which is what the runner uses).
    """
    normalized = validate_mapping(gt_files, mapping, registered_rule_ids)
    names = list(names) if names is not None else sorted(gt_files)

    numbers = {n: (gt_files[n].get("invoice_number") or None) for n in names}
    gt_sets = {
        n: set(gt_files[n].get("anomalies") or []) for n in names
    }

    # -- attribute every finding once ---------------------------------------
    attributed: list[tuple[Any, list[str]]] = []
    unattributed: list[dict] = []
    unmapped: Counter[str] = Counter()
    mapped_ids = {ref.rule_id for refs in normalized.values() for ref in refs}

    for finding in findings:
        rule_id = _field(finding, "rule_id")
        if rule_id not in mapped_ids:
            unmapped[rule_id] += 1
        targets = attribute_finding(finding, names, numbers)
        if targets:
            attributed.append((finding, targets))
        else:
            unattributed.append(
                {
                    "rule_id": rule_id,
                    "invoice_index": _field(finding, "invoice_index"),
                    "invoice_number": _field(finding, "invoice_number"),
                }
            )

    # -- per anomaly class --------------------------------------------------
    classes: dict[str, dict] = {}
    for cls, refs in sorted(normalized.items()):
        gt_set = {n for n in names if cls in gt_sets[n]}
        pred: set[str] = set()
        for finding, targets in attributed:
            if any(ref.matches(finding) for ref in refs):
                pred.update(targets)
        metrics = _prf(len(pred & gt_set), len(pred - gt_set), len(gt_set - pred))
        metrics.update(
            {
                "support": len(gt_set),
                "rules": [str(ref) for ref in refs],
                "tp_invoices": sorted(pred & gt_set),
                "fp_invoices": sorted(pred - gt_set),
                "fn_invoices": sorted(gt_set - pred),
            }
        )
        classes[cls] = metrics

    # -- per rule (field selectors ignored: did the rule fire or not?) ------
    rules: dict[str, dict] = {}
    for rule_id in sorted(mapped_ids):
        gt_set = {
            n
            for n in names
            if any(rule_id in {r.rule_id for r in normalized[c]} for c in gt_sets[n])
        }
        pred = {
            target
            for finding, targets in attributed
            if _field(finding, "rule_id") == rule_id
            for target in targets
        }
        metrics = _prf(len(pred & gt_set), len(pred - gt_set), len(gt_set - pred))
        metrics.update(
            {
                "support": len(gt_set),
                "backing_classes": sorted(
                    c for c, refs in normalized.items()
                    if rule_id in {r.rule_id for r in refs}
                ),
                "tp_invoices": sorted(pred & gt_set),
                "fp_invoices": sorted(pred - gt_set),
                "fn_invoices": sorted(gt_set - pred),
            }
        )
        rules[rule_id] = metrics

    # -- overall (micro over (invoice, class) pairs) ------------------------
    tp = sum(m["tp"] for m in classes.values())
    fp = sum(m["fp"] for m in classes.values())
    fn = sum(m["fn"] for m in classes.values())
    overall = _prf(tp, fp, fn)
    defined = [m for m in classes.values() if m["precision"] is not None]
    overall["macro_precision"] = (
        round(sum(m["precision"] for m in defined) / len(defined), 4) if defined else None
    )
    defined_r = [m for m in classes.values() if m["recall"] is not None]
    overall["macro_recall"] = (
        round(sum(m["recall"] for m in defined_r) / len(defined_r), 4) if defined_r else None
    )
    exact = sum(1 for n in names if {
        c for c in gt_sets[n]
    } == {
        c for c, m in classes.items() if n in set(m["tp_invoices"]) | set(m["fp_invoices"])
    })
    overall["invoice_exact_match"] = round(exact / len(names), 4) if names else None
    overall["n_classes"] = len(classes)
    overall["n_invoices"] = len(names)

    # -- loud diagnostics ---------------------------------------------------
    unmatched = [
        str(ref)
        for cls, refs in sorted(normalized.items())
        for ref in refs
        if classes[cls]["support"] > 0
        and not any(ref.matches(f) for f, _ in attributed)
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "n_invoices": len(names),
        "n_annotated_invoices": sum(1 for n in names if gt_sets[n]),
        "n_anomaly_labels": sum(len(gt_sets[n]) for n in names),
        "n_classes": len(classes),
        "mapping": mapping_as_dict(normalized),
        "file_order": names,
        "classes": classes,
        "rules": rules,
        "overall": overall,
        "findings_total": len(findings),
        "unmapped_rule_findings": dict(sorted(unmapped.items())),
        "unattributed_findings": unattributed,
        "unmatched_selectors": unmatched,
    }


def evaluate_ground_truth_audit(
    gt_files: Mapping[str, dict] | None = None,
    *,
    names: Sequence[str] | None = None,
    engine: Any = None,
    settings: Any = None,
    mapping: Mapping[str, Iterable[Any]] | None = None,
) -> dict:
    """Run the real audit engine over the shipped ground truth and score it."""
    if gt_files is None:
        gt_files = load_ground_truth()
    names = list(names) if names is not None else sorted(gt_files)

    if engine is None:
        from app.services.audit.engine import AuditEngine

        engine = AuditEngine(settings) if settings is not None else AuditEngine()

    docs = [InvoiceDocument.model_validate(gt_files[n]) for n in names]
    findings = engine.run(docs)

    report = evaluate_audit(
        gt_files, findings, names=names, mapping=mapping
    )
    report["engine"] = getattr(engine, "name", type(engine).__name__)
    report["rule_errors"] = list(getattr(engine, "errors", []) or [])
    return report


# --- markdown --------------------------------------------------------------

_LIMITATIONS = """\
## Honest limitations

Read these before quoting the numbers above.

- **Small sample.** {n_invoices} invoices, {n_anomaly_labels} injected
  anomalies across {n_classes} classes — some classes carry a single example.
  A per-class recall of 1.0 on a support of 1 is one data point, not evidence
  of generalisation.
- **Injected, not observed.** Every label is written by
  `scripts/generate_synthetic_invoices.py` *and* every invoice is generated
  with clean, machine-printed fields. The audit rules fire on exactly the
  quantity the generator perturbed, so this measures "do the rules detect
  known perturbations", not "do the rules catch fraud as it occurs".
- **Not a production distribution.** Real batches contain skewed tax rates,
  red-ink invoices, voided invoices, multi-page scans, low-quality photos and
  partial extractions. None of that is represented here, so precision in
  particular should be expected to fall on real data.
- **One batch.** The duplicate-number rule is batch-scoped; whether it fires
  depends on which invoices share a batch. This evaluation uses a single
  30-invoice batch, so batch-composition sensitivity is untested.
- **No held-out split.** The mapping was written against this same file, so
  the numbers are in-sample by construction. Nothing here is a generalisation
  estimate.
- **Rule-level proxy.** A finding is credited to an invoice via
  `invoice_index`/`invoice_number`. An anomaly class backed by a rule that can
  fire for *other* reasons (e.g. `party_info` also warns on malformed or
  checksum-failing tax ids) will score those as false positives unless a field
  selector narrows the class — which is exactly why the mapping carries
  selectors.
- **No negative-label accounting.** The ground truth records the
  `{n_anomaly_labels}` injected anomalies; it does not assert that the
  remaining `{n_invoices}` invoices are clean. Any rule that fires on an
  unlabelled invoice therefore counts as a false positive, and that judgement
  is the generator's intent rather than an auditor's verdict — the count above
  is only as good as those labels.
"""


def render_audit_markdown(report: Mapping[str, Any]) -> str:
    """Render the audit evaluation report as the `docs/audit-eval.md` page."""
    overall = report["overall"]
    fmt = lambda v: "—" if v is None else f"{v:.4f}"  # noqa: E731

    lines = [
        "# Audit-engine evaluation",
        "",
        "> Generated by `python scripts/run_audit_eval.py` — do not edit by hand.",
        "",
        "The extraction benchmark ([benchmark.md](benchmark.md)) scores *fields*.",
        "This page scores *decisions*: does the rule engine flag the invoices that",
        "`benchmark/ground_truth.json` annotates as anomalous?",
        "",
        "## Evaluation set",
        "",
        f"- **Invoices**: {report['n_invoices']} synthetic Chinese e-invoices "
        "(fully fabricated — see [data-compliance.md](data-compliance.md))",
        f"- **Annotated invoices**: {report['n_annotated_invoices']} "
        f"({report['n_anomaly_labels']} anomaly labels, {report['n_classes']} classes)",
        f"- **Engine**: `{report.get('engine', 'AuditEngine')}` — all registered "
        "rules enabled, default thresholds",
        f"- **Findings emitted**: {report['findings_total']}",
        f"- **Rule errors**: {len(report.get('rule_errors') or [])}",
        "",
        "## Anomaly class → rule mapping",
        "",
        "| Anomaly class (ground truth) | Rule(s) |",
        "|---|---|",
    ]
    for cls, refs in report["mapping"].items():
        lines.append(f"| `{cls}` | {', '.join(f'`{r}`' for r in refs)} |")

    lines += [
        "",
        "The mapping is data, not code: an anomaly class present in the ground",
        "truth but absent from the mapping, or a mapping entry naming an",
        "unregistered rule id, raises `AuditEvalError` instead of scoring 0.",
        "",
        "## Per-class precision / recall",
        "",
        "| Anomaly class | support | TP | FP | FN | precision | recall | F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cls, m in report["classes"].items():
        lines.append(
            f"| `{cls}` | {m['support']} | {m['tp']} | {m['fp']} | {m['fn']} | "
            f"{fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} |"
        )
    lines += [
        "",
        "Counted per **invoice**, not per finding: a rule that emits three",
        "findings for one bad invoice earns one true positive.",
        "",
        "## Per-rule precision / recall",
        "",
        "| rule_id | backing classes | support | TP | FP | FN | precision | recall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rule_id, m in report["rules"].items():
        lines.append(
            f"| `{rule_id}` | {', '.join(m['backing_classes'])} | {m['support']} | "
            f"{m['tp']} | {m['fp']} | {m['fn']} | {fmt(m['precision'])} | "
            f"{fmt(m['recall'])} |"
        )

    lines += [
        "",
        "## Overall",
        "",
        f"- **Micro precision**: {fmt(overall['precision'])} "
        f"({overall['tp']} TP / {overall['tp'] + overall['fp']} predicted labels)",
        f"- **Micro recall**: {fmt(overall['recall'])} "
        f"({overall['tp']} TP / {overall['tp'] + overall['fn']} ground-truth labels)",
        f"- **Micro F1**: {fmt(overall['f1'])}",
        f"- **Macro precision** (over classes where it is defined): "
        f"{fmt(overall['macro_precision'])}",
        f"- **Macro recall**: {fmt(overall['macro_recall'])}",
        f"- **Invoice-level exact match**: {fmt(overall['invoice_exact_match'])} "
        "— invoices whose predicted anomaly-class set equals the labelled set",
        "",
    ]

    unmapped = report.get("unmapped_rule_findings") or {}
    if unmapped:
        lines += [
            "Findings from rules that back no anomaly class (reported, not",
            "counted as false positives): "
            + ", ".join(f"`{k}` × {v}" for k, v in unmapped.items()),
            "",
        ]
    if report.get("unattributed_findings"):
        lines += [
            f"Unattributed findings: {len(report['unattributed_findings'])} "
            "(no resolvable invoice index or number — never credited).",
            "",
        ]
    if report.get("unmatched_selectors"):
        lines += [
            "Mapping selectors that matched no finding while their class has",
            "support (a silent-recall warning worth investigating): "
            + ", ".join(f"`{s}`" for s in report["unmatched_selectors"]),
            "",
        ]
    if report.get("rule_errors"):
        lines += [
            "**Rule errors** (a crashed rule is isolated, never silently",
            "dropped, but it does make this run non-conclusive):",
            "",
            "```json",
            json.dumps(report["rule_errors"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]

    lines.append(_LIMITATIONS.format(**report))
    return "\n".join(lines) + "\n"
