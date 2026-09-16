# Upgrade notes — audit evaluation, compliance fix, failure semantics, rule isolation

Four changes to DocMind, each done test-first (red → green). Every number below
comes from a command actually run in this working tree; nothing is estimated.

Verification baseline: `pytest` → **186 passed** (132 pre-existing tests all
still green, +54 new). `npm run build` (tsc -b && vite build) → success.
No `git push` was performed; all work is local.

---

## A. The audit engine was never measured

**Problem.** `benchmark/ground_truth.json` already annotated 12 of its 30
invoices with 7 classes of injected anomaly, and no code in the repository
consumed those labels. The only evaluation covered *field extraction*
(`app/services/eval/metrics.py`), so the rule engine — the project's actual
differentiator — had no quality number at all. A rules engine with no
measured precision/recall is a claim, not a feature.

**Test (red).** `tests/test_audit_eval.py` written first and run:

```
$ pytest tests/test_audit_eval.py
ModuleNotFoundError: No module named 'app.services.eval.audit_metrics'
ERROR tests/test_audit_eval.py::...
```

The suite pins the parts that matter: an anomaly class present in the ground
truth but absent from the mapping raises `AuditEvalError`; a mapping entry
naming an unregistered rule id raises (a typo must not score 0); undefined
precision is reported as `None`, never as a flattering 1.0; an empty ground
truth, or one with no anomalies at all, refuses to report metrics.

**Fix.** `app/services/eval/audit_metrics.py` (mapping + invoice-level per-class
and per-rule precision/recall) and `scripts/run_audit_eval.py` (offline CLI, no
API key, no cost). Placed under `app/services/eval/` rather than
`benchmark/eval_audit.py` so it mirrors the existing `eval/metrics.py` +
`scripts/run_benchmark.py` split and stays importable by the test-suite.

Two design points worth calling out:

- The mapping (`ANOMALY_TO_RULES`) is data, and `RuleRef` may carry a **field
  selector**. `party_info` backs *two* anomaly classes; without selectors,
  `self_dealing`'s finding would be scored as a false positive against
  `missing_seller_tax_id` and drag both classes' precision to 0.5. The
  selectors are `seller.tax_id` and `buyer.name`.
- Counts are per **invoice**, not per finding: a rule emitting three findings
  for one bad invoice earns one true positive.

**Real numbers** (`python scripts/run_audit_eval.py`, report in
[docs/audit-eval.md](audit-eval.md)):

| metric | value |
|---|---|
| invoices / annotated / labels / classes | 30 / 12 / 12 / 7 |
| findings emitted by the engine | 10 |
| micro precision / recall / F1 | 1.0000 / 1.0000 / 1.0000 (tp=12, fp=0, fn=0) |
| invoice-level exact match | 30/30 = 1.0000 |
| rule errors | 0 |

Per class: `duplicate_number` support 4, `arithmetic_mismatch` 2,
`anomalous_tax_rate` 2, and `missing_seller_tax_id`, `self_dealing`,
`qr_mismatch`, `future_date` 1 each — all with 0 FP and 0 FN. Note that 10
findings cover 12 invoices because each duplicate-number finding names a pair.

**Honest limitations** (written into the generated report, not buried): n=30
with single-example classes, anomalies are *injected* by the same generator
that produced the invoices, no production-like distribution (no scans, photos,
voids, partial extractions), a single batch for a batch-scoped rule,
in-sample mapping with no held-out split, and no negative-label accounting
(unlabelled invoices count as clean without an auditor having asserted it).

**Interview angle.** "The repository already had ground-truth labels nobody
consumed" is a concrete example of turning an unmeasured claim into a number.
The two defensible bits are the *loud failure* design (a broken mapping raises
rather than reporting a perfect 0/0) and the shared-rule/field-selector
problem, which is where naive per-rule scoring silently lies.

---

## B. Compliance red line: real PII committed to a public repo

**Problem.** `docs/data-compliance.md` lists real identifiers from the original
coursework dataset and declares they must never be committed — while
`tests/conftest.py` and five other test modules hard-coded four of them (a real
university name, two real unified social credit codes, a real issuer's name).
They were already on public `master`. The document's guarantee was false, which
is worse than having no document.

**Test (red).** `tests/test_pii_guard.py` written first and run:

```
$ pytest tests/test_pii_guard.py
FAILED TestScannerSelfTest::test_needle_list_is_not_silently_shrunk
FAILED TestRepositoryIsClean::test_no_documented_pii_literal_in_the_repository
E  Failed: documented PII literals found in the repository ...
E    docs\data-compliance.md: <12 literals>
E    tests\conftest.py: <4 literals>
E    tests\test_audit_rules.py: <3 literals>
E    tests\test_extraction.py: <4 literals>
E    tests\test_graph.py: <2 literals>
E    tests\test_schema.py: <4 literals>
E    tests\test_uscc.py: <5 literals>
```

**Fix.** Replace the values — not the test. The needles are base64-encoded so
the scanner cannot flag itself, and the scanner carries positive controls (a
planted literal is detected, an ignored directory is skipped, the needle list
cannot be quietly shrunk). Replacements keep every test meaningful:

- same lengths and shapes (18-character tax ids stay 18 characters);
- valid GB 32100-2015 check characters, so a clean default invoice still passes
  `party_info` (three existing tests depend on that);
- a wrong-check-character variant that the deterministic repair still fixes;
- the "organisation code" section is all zeros, so the codes cannot belong to a
  registered entity;
- synthetic identities live in **one place** now (`tests/conftest.py`:
  `BUYER_NAME`, `BUYER_TAX_ID`, `SELLER_TAX_ID`, `ISSUER`, …).

`docs/data-compliance.md` no longer reproduces the literals (it records
category, shape and count instead) and gained a *Remediation* section
documenting that the leak happened. `_source-archive/` was not touched.

**Test (green).** `5 passed`.

**Interview angle.** "Your own compliance document was aspirational" is a
common and serious finding. The reusable pattern is making a *policy* into an
*executable check* with a positive control, so it cannot rot.

---

## C. A fully failed batch reported a clean audit

**Problem (measured).** With every document failing to extract, the batch
reported `status=done`, `done=1/1`, `errors=[]`, and the audit endpoint
returned an empty findings list next to a normal-looking status. A reviewer —
or any downstream consumer — reads that as "this batch is clean". This is the
single most dangerous failure mode an audit product can have.

**Root cause.** `done = sum(1 for r in results if r is not None)`. A failed
extraction still stores a result dict (with `success: false`), so failures were
counted as completions; the terminal status was then written unconditionally.
The same bug existed in all three write paths (background batch, retry, demo).

**Test (red).** `tests/test_failure_semantics.py` written first and run:

```
$ pytest tests/test_failure_semantics.py
10 failed, 1 passed
E   AssertionError: an all-failed batch must not report success
E     assert 'done' != 'done'          <- the reported symptom
E   assert 2 == 0                      <- `done` counted the failures
E   assert 0 == 2                      <- errors == []
E   KeyError: 'status' / 'audit_conclusive' / 'audited_documents' / 'failed'
```

**Fix.** `_summarize_results()` counts successes only and collects
`{filename, error}` per failure; `_batch_status()` marks a pass `failed` when
nothing extracted at all. Applied to `_run_batch`, `retry_batch` and demo load.
`summarize()` carries `documents_audited`, so "zero findings over zero
documents" is distinguishable from "zero findings over a full batch". The audit
endpoint now returns `status`, `done`, `failed`, `errors`, `audited_documents`
and an explicit `audit_conclusive` flag (true only when the batch finished, no
document failed, no rule crashed, and every document was audited). The frontend
shows a banner where an empty audit panel would otherwise imply "clean".

**Test (green).** `11 passed`; full suite `186 passed`.

**Interview angle.** The interesting part is that the *type* was wrong, not the
arithmetic: `r is not None` conflated "processed" with "succeeded". And the fix
is a contract change (`audit_conclusive`), not just a counter change — the
caller must be able to tell "no anomalies" from "we could not tell".

---

## D. One broken rule discarded the entire audit

**Problem (measured).** An exception inside a single rule propagated out of
`AuditEngine.run`, discarding every finding from every rule; at the HTTP layer
it failed the whole batch. A typo in one rule therefore silently removed the
fraud signal of the other seven — strictly worse than the rule not existing,
because the batch still reported `status=done` with an empty findings list.

**Test (red).** `tests/test_rule_isolation.py` written first and run:

```
$ pytest tests/test_rule_isolation.py
13 failed
E   RuntimeError: exploding exploded                      <- propagated out of run()
E   AttributeError: 'AuditEngine' object has no attribute 'errors'
E   KeyError: 'rule_errors'
```

Stub rules subclass `AuditRule` with an empty class-level `rule_id`, so the
registry's auto-registration hook leaves the global rule registry untouched
(important: a permanently-broken registered rule would poison every other test
in the session).

**Fix.** Each rule runs in isolation in `AuditEngine.run`. A raising rule is
skipped and recorded as `RuleError(rule_id, rule_name, error)` on
`AuditEngine.errors` (reset per run, never accumulated); the remaining rules
still produce findings, still sorted by severity. Disabled rules are neither
run nor reported as broken. `rule_errors` flows through to the batch and the
audit endpoint, and a crashed rule makes `audit_conclusive` false: the run
stays observable, but it is not a clean bill of health.

**Test (green).** `13 passed`. End-to-end coverage breaks a *real* registered
rule (`ArithmeticTotalRule`) via monkeypatch and asserts the findings of the
other five rules that fire on this batch (8 of the usual 10 findings) still
reach `/audit` on the full 30-invoice demo batch, with `rule_errors` naming
`arithmetic_total` — verified directly:

```
healthy: findings = 10  rule_errors = []  status = done
broken : findings =  8  rule_errors = [{rule_id: arithmetic_total, ...}]  status = done
```

**Disclosed.** After implementing, the first green run still had failures —
all in my own test code, none in the implementation, and none fixed by
loosening an assertion: (1) three assertions subscripted `engine.errors` as
dicts while the engine exposes a dataclass (the JSON shape is asserted
separately at the API level, so nothing was dropped); (2) a class-body
`rule_id = ""` shadowed the closure variable, making `name` resolve to
`"stub "` instead of `"stub exploding"`. Both were test-stub bugs, fixed in the
test file.

**Interview angle.** Blast-radius control: proving that a partial failure
degrades one rule instead of the batch, and that the degradation is *reported*
rather than absorbed. The registry-pollution trap above is also a good story
about test isolation.

---

## Notes, deviations and open items

- **Task A file placement** deviates from the suggested `benchmark/eval_audit.py`:
  logic lives in `app/services/eval/audit_metrics.py` (importable, testable,
  consistent with the existing `eval/metrics.py`) with
  `scripts/run_audit_eval.py` as the CLI, mirroring `run_benchmark.py`.
- **README section** for the audit evaluation is 15 lines, as requested.
- **"Sample data is synthetic"** was already stated in the README
  (§ *Privacy-safe samples* and the Disclaimer), so it was not duplicated;
  instead `docs/data-compliance.md` gained the remediation record.
- **Scope boundary on Task B:** the invoice number `24417000000034170288` and
  the 20-digit check code `51191401325570116214` come from the same original
  sample invoice but are *not* in the documented PII categories (the compliance
  audit lists corporate names, tax ids, personal names, bank accounts, order
  numbers, credentials). They were left as-is to avoid rewriting unrelated
  assertions; if the intent is "no byte of the original dataset", they should be
  replaced too — flagged rather than silently decided.
- `benchmark/results/audit_eval.json` is written by the runner but git-ignored
  (`benchmark/results/`); the tracked artifact is `docs/audit-eval.md`, which
  contains every number.
