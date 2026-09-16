# Upgrade notes — audit evaluation, compliance fix, failure semantics, rule isolation

Six changes to DocMind, each done test-first (red → green). Every number below
comes from a command actually run in this working tree; nothing is estimated.

Verification baseline: `pytest` → **186 passed** (132 pre-existing tests all
still green, +54 new) for sections A–D; **296 passed** after sections E and F
(251 before section F, +45 new tests). `npm run build` (tsc -b && vite build) →
success. No `git push` was performed; all work is local.

> A fifth change is documented in [Section E](#e-the-end-to-end-measurement-the-audit-page-deferred)
> below: the end-to-end evaluation, which closes the limitation section A
> admitted and reports a much lower — and more useful — number.
>
> A sixth change is documented in [Section F](#f-the-122-extraction-failure-rate--measured-fixed-re-measured):
> the 12.2% extraction-failure rate that section E measured, reproduced, fixed
> and then re-measured — including a defect the first fix introduced that only
> the re-measurement caught.

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

## E. The end-to-end measurement the audit page deferred

> The numbers in this section are the **pre-fix** measurement, preserved as the
> baseline it is: the extraction-failure rate it reports (12.2%) is what
> [section F](#f-the-122-extraction-failure-rate--measured-fixed-re-measured)
> then fixed and re-measured. Both rounds are published side by side on
> [e2e-eval.md](e2e-eval.md).

**Problem.** Section A shipped an honest limitation: the audit engine scored
1.000, but it was scored over the *labelled* field values in
`benchmark/ground_truth.json`, not over what the vision model actually returned
for the PDFs. That is the first thing an interviewer asks about a document-AI
project — *does the audit still work when the OCR is wrong?* — and the honest
answer was "not measured". The gap also had a known shape on both sides: a
mis-OCR'd amount can hide a real anomaly (the rule sees a consistent but wrong
document) and can manufacture a false one.

**Design.**

- `app/services/eval/e2e.py` — pure and offline, mirroring the existing
  `eval/metrics.py` / `eval/audit_metrics.py` layout:
  - `UsageMeter`: thread-safe per-call token accounting with a hard budget
    fuse. It is fed by the extractor, so it must never raise (the extractor's
    `except Exception` would turn a budget abort into an ordinary extraction
    failure); the runner calls `check_budget()` at a safe point instead.
  - `summarize_rounds`: mean/min/max/range per metric, with range reported as
    `None` — not `0.0` — when only one round exists, because one observation
    cannot express spread.
  - `classify_divergences`: every difference between the labelled-field run and
    the end-to-end run, per invoice and anomaly class, with a `mechanism` label
    (`masked_value_error`, `masked_partner_lost`, `manufactured_value_error`,
    `masked_unexplained`, …), the driving field values that changed, and — for
    batch-scoped rules — the state of the partner invoices.
  - `render_e2e_markdown`, `redact_endpoint`.
- `app/services/extraction/dashscope_extractor.py` — one additive
  `usage_recorder` hook, called **before** JSON parsing so a response that is
  billed and then discarded is still charged. A test asserts the request shape
  (prompt text, `temperature=0.0`, `response_format`) is byte-for-byte
  unchanged by the hook; the prompt itself was not touched.
- `scripts/run_e2e_eval.py` — 30 invoices × 3 rounds, the project's own
  extractor, `--budget-cny` fuse, `--render-only` to re-render the page from
  the artifact at zero cost.

Two defects in the *measurement itself* surfaced while running it, and both
were fixed test-first:

1. `evaluate_audit` resolved batch-level findings (`dup_invoice_number`) by the
   **ground-truth** invoice numbers. On extracted input a misread number makes
   a genuine finding unresolvable, so the very error under test was scored as a
   missed detection. Added an optional `numbers=` override; the end-to-end run
   passes the numbers the engine actually saw. (Verified: `unattributed` is 0
   in all three rounds.)
2. Field accuracy over the auditable subset is not comparable with the recorded
   0.8249 baseline, which counts a failed extraction as an empty document.
   The runner now records **both** conventions and the page prints them side by
   side with the delta.

**Results** (real API, same batch, `qwen-vl-plus`, three rounds).

| Metric | Labelled fields | Real extraction (auditable subset) | Real extraction (failures = misses) |
|---|---|---|---|
| micro precision | 1.0000 | 0.6579 | 0.6579 |
| micro recall | 1.0000 | 0.8065 | 0.6944 |
| micro F1 | 1.0000 | 0.7247 | 0.6757 |

Per-round micro F1 was 0.800 / 0.696 / 0.667 (subset) and 0.769 / 0.640 /
0.609 (full batch) — the spread is real, not noise-free, and comes mostly from
which invoices happened to fail extraction that round.

- **Field accuracy.** 0.9421 mean over the auditable subset (0.9399–0.9444);
  0.8272 mean with failed extractions counted as empty documents
  (0.8135–0.8470) — **+0.23 pp against the recorded 0.8249 baseline**, i.e. the
  earlier field number reproduces on this batch.
- **Extraction failures.** 11 of 90 attempts (12.2 %): 3 / 4 / 4 per round.
  *Every single one* had the same cause — `malformed JSON in model response:
  Expecting ',' delimiter` from the primary model on both attempts, after which
  the fallback was rejected by the endpoint with `403 insufficient_quota`. The
  failure is per-invoice, not per-run: 6 distinct files failed at least once and
  3 of them succeeded in another round.
- **Masked instance (reproduced 3/3 rounds).** `invoice_020.pdf` is a labelled
  `arithmetic_mismatch`: the labelled document says 630.18 + 75.91 but declares
  656.09. The model returned the **self-consistent** total 706.09, so
  `arithmetic_total` sees a coherent document and cannot fire. The same misread
  simultaneously **manufactured** a `qr_mismatch` finding — the QR payload,
  decoded from the pixels, still says ¥656.09 — which is the strongest argument
  in the whole run for cross-source rules over same-source ones.
- **Manufactured instance (2 of 3 rounds).** `invoice_011.pdf` is clean
  (14060.18 + 1301.90 = 15362.08). The model read `tax_amount` as 1299.90
  (round 3: 1291.90), so `arithmetic_total` raised an **ERROR** with a ¥2.00
  (round 3: ¥10.00) discrepancy against a ¥0.02 tolerance — 100–500× the
  threshold, which is why no tolerance tuning could have prevented it.
- **Manufactured instance, second mechanism.** `invoice_004.pdf`'s seller tax
  id was read as `91370000WGE377ZA0G` (a `2` became `Z`). GB 32100-2015 excludes
  `I O Z S V` from the code alphabet, so the value is not merely
  check-character-wrong: `repair_uscc` deliberately declines to repair it and
  `party_info` reports a checksum failure on a clean invoice. A deterministic
  lookalike map (`O→0, I→1, Z→2, S→5, V→U`) plus checksum verification repaired
  **both observed cases exactly** (`Z→2` and `O→0` each reproduced the labelled
  id character-for-character), and the checksum gate is what makes the repair
  safe rather than a guess.
- **Batch-scoped fragility.** `dup_invoice_number` recall fell to 0.667 and
  then 0.0 purely because one invoice of a duplicate pair failed extraction and
  was dropped: the survivor's number becomes unique, so the rule cannot fire.
  The classifier names this `masked_partner_lost` rather than reporting it as
  "unexplained" — the first run's output, before the classifier knew about
  batches, would have made a false accusation against the harness.
- **Cost and latency.** ¥0.3605 of list price over 117 calls
  (198,549 input / 100,842 output tokens) against the ¥25 ceiling — 1.4 % of
  budget. 419 s wall clock at 4 workers, 16.69 s mean per invoice, 11.3
  invoices/min. Linear extrapolation to 100 k invoices: **≈¥401 and ≈147 hours
  at 4 workers**, plus **≈12,200 documents** that would still need a human or a
  second model pass at the measured 12.2 % failure rate.
- **What did not degrade.** `tax_rate`, `self_dealing` and `invoice_date` held
  precision and recall at 1.0 across all three rounds; the damage is
  concentrated in the rules that read the fields the model misreads most
  (`amount_including_tax`, tax ids, invoice numbers).

**Interview angle.**

- *The negative result is the deliverable.* 1.000 was an upper bound; the
  measured pipeline scores F1 0.68–0.72. Saying that before being asked is worth
  more than defending the 1.000.
- *Separate engine quality from pipeline reliability.* Precision loss (1.000 →
  0.658) is entirely OCR-driven: every false positive is a rule correctly
  describing a document that was read wrong. Recall loss splits cleanly into
  documents that were never read (12.2 %) and signals that were read away
  (masked). Those need different fixes, so they are reported separately.
- *Measurement code needs the same scrutiny as product code.* Two of the three
  defects found in this task were in the harness — ground-truth-based
  attribution and the subset-vs-full-batch convention — and both would have
  published a wrong number. Finding them is the argument for building the
  harness test-first rather than scripting it.
- *Cost is not the constraint; reliability is.* ¥401 for 100 k invoices is
  negligible next to the 12,200 documents that would land in a human queue. The
  optimisation target is the JSON decode path, not the token bill.

---

## F. The 12.2% extraction-failure rate — measured, fixed, re-measured

**Symptom.** Section E's measurement produced the number this section exists
for: **11 of 90 extraction attempts (12.2%) failed outright**, every single one
with the same message — `malformed JSON in model response: Expecting ',' 
delimiter` from the primary model, on both attempts, after which the configured
fallback was refused by the endpoint (`403 insufficient_quota`). An extraction
failure is not a misread field: the document never reaches the audit engine, so
every anomaly label on it is scored as missed. That made the *decode path* — not
the audit rules — the binding constraint on end-to-end recall, and section E's
own recommendation list called it "the single highest-value change in this list".

**Reproduce before fixing.** The artifact recorded the failure *text*; the raw
response had been thrown away. So the first step was to make the defect
reproducible rather than guessed at: a throwaway probe (`.probe/capture_raw.py`,
untracked scratch) replayed the failing invoices through the project's own
prompt, DPI and sampling parameters and dumped the raw content. Three of those
responses are now committed as fixtures under `tests/fixtures/model_responses/`,
each one named for the invoice it came from in the test docstring. That is what
the first red test ran against.

**Root cause — framing damage, not missing content.** Both shapes have the whole
document in them; only the JSON framing is broken:

1. **The outer object is never closed.** The model emits the complete body and
   then rambles whitespace instead of writing the final `}`. The recorded
   `char 743` / `char 716` errors are all at the end of the output.
2. **A repeated tail.** The model re-emits members it already emitted and is cut
   off mid-string, so `text[first "{"] : text[last "}"]` — the old decode path —
   grabs a brace from inside the repetition and glues two fragments together into
   something no parser accepts.
3. **A repeated *skeleton*.** Found later, and it is the one that mattered most:
   the model repeats the line-item template (`"项目名称": "", "数量": 0,
   "金额": 0`) **out of the array it belongs to**, as top-level keys. Python's
   `json` keeps the *last* occurrence of a duplicated key, so the placeholder
   overwrote the number the model had already read correctly.

**Fix** (three layers, cheapest first; every one of them test-first):

- `json_utils._scan` — a small tokenizer that reports *how* a payload fails, and
  specifically distinguishes **truncation** (input ended with containers still
  open — the body was emitted, so closing it loses nothing) from **contradiction**
  (`{"a": 1, "b": }` — any repair would have to invent a value, so it is
  refused). `_closed_truncation` cuts back to the last *complete* outermost
  member and closes the object: an unterminated member carries no value, so
  nothing is dropped that was ever there.
- `_decode` — duplicate keys resolve **first-occurrence-wins** instead of
  `json`'s last-wins. The repetition is degeneration, not a correction; the
  first emission is the reading. This is the fix for root cause 3, and it was
  added only after the first re-measurement showed predicted amounts of `0.0`
  carrying model-reported confidence 1.0.
- `DashScopeExtractor` — a response that is still unusable (or that parses into
  a document with **no invoice fields at all**) gets one corrective re-read: the
  model's own output is replayed as its previous turn, followed by the parser's
  complaint. A plain retry at temperature 0 is what reproduced the identical
  defect in the measured run, so the correction is sampled (0.2) and bounded at
  two calls per model. A model that *refuses* the request is now reported as
  `model unavailable (request refused, no document read)` and accounted for in
  the cost table with `ok=false`, instead of being folded into "2 tried".
- `has_auditable_content` — the gate that stops a repair from turning "the model
  never emitted the document" into a syntactically perfect, empty invoice. That
  would report a read that never happened, which is the one failure a repair must
  not create.

**Results** (same 30 invoices, same 3 rounds, same parameters).

| Metric | before | after | change |
|---|---|---|---|
| **Extraction failure rate** | **12.22%** (11/90) | **0.00%** (0/90) | **−12.22 pp** |
| Invoices read | 79/90 | 90/90 | +11 |
| micro F1 (failures counted as missed) | 0.6757 | **0.8148** | **+0.1391** |
| micro precision (same convention) | 0.6579 | 0.7333 | +0.0754 |
| micro recall (same convention) | 0.6944 | 0.9167 | +0.2223 |
| micro F1 (auditable subset) | 0.7247 | 0.8148 | +0.0901 |
| Field accuracy, whole batch (failures as empty) | 0.8272 | **0.9411** | **+11.39 pp** |
| Field accuracy, auditable subset | 0.9421 | 0.9417 | −0.0004 |
| API calls | 117 | 91 | −26 |
| Cost, list price | ¥0.3605 | ¥0.3138 | −¥0.0468 |
| Wall clock / mean per invoice | 419.4s / 16.69s | 299.3s / 12.86s | −29% / −23% |
| Throughput at 4 workers | 11.3 inv/min | 18.04 inv/min | +60% |

- **45.6% of responses (41/90) needed a framing repair**; only **one** response
  in the whole run was unusable even after repair, and the corrective re-read
  recovered it. The fallback model was never needed (`fallback_reads = 0`), and
  the run cost *fewer* calls than before, because a repaired response no longer
  burns a blind retry.
- **Per-round micro F1 is now identical in all three rounds** (0.8148 / 0.8148 /
  0.8148, range 0.0000, against 0.7692 / 0.6400 / 0.6087 before). The largest
  source of run-to-run variance was never the model's sampling — it was *which
  invoices happened to fail that round*. Removing the failure mode removed the
  variance with it, which also makes every future comparison on this batch
  meaningful.
- **The residual loss is now entirely OCR.** Masked = 1 in every round and it is
  the same invoice every time (`invoice_020.pdf`, `masked_value_error`: the model
  read a self-consistent but wrong total, so `arithmetic_total` cannot fire).
  Manufactured = 4 per round, the same four, all `qr_crosscheck` / `party_info`
  false alarms on misread amounts and tax ids. The `masked_partner_lost` cases
  that section E saw in rounds 2 and 3 — where a `duplicate_number` label was
  lost purely because one invoice of the pair failed extraction and was dropped —
  are gone, because nothing is dropped any more.
- **One row is not comparable and the page says so.** *Field accuracy —
  auditable subset* is flat (0.9421 → 0.9417), but the population changed: the
  baseline excluded the 3–4 documents per round it could not read; this run
  excludes none. The previously unreadable documents scored at about the batch
  average. The comparable row is the whole batch with failures counted as
  misses: **+11.39 pp**. This is exactly the trap section E warned about —
  reporting only the auditable subset would have made the fix look like nothing.

**The fix was wrong once, and re-measuring is what caught it.** The first
post-fix run reported 0.00% failures, F1 0.6757 → 0.7333, and looked finished.
Drilling into the *values* rather than the headline found 18 fields predicted as
`0.0` while the model reported confidence 1.0 — the pre-fix run had none. A
forensics probe (`.probe/forensics_zero_amounts.py`) reproduced the shape in 4 of
21 live responses and dumped the payloads: every one of them arrived via the
truncation repair, and every one was root cause 3 above — the model's own
placeholder skeleton, kept by last-key-wins, overwriting the number it had
actually read (`金额: 70.3` → `0`). Red test on the captured payload, then
first-occurrence-wins, then re-run. The superseded artifact and the captured
payloads are kept on disk (`benchmark/results/e2e_eval_after_run1_superseded.json`,
`.probe/forensics/`); this is why the published "after" numbers are from the
second run and not the first.

**What is *not* claimed.**

- The failure rate is 0/90 **on this batch, this model and this endpoint**. It
  is not a claim that the model cannot produce unusable output; it is a claim
  that this pipeline now recovers from the shapes it observed, and that the
  recovery is recorded per document (`doc.corrections["json"]`) so a future run
  can prove it was exercised rather than assume it.
- The `qwen3.5-ocr` fallback is still non-functional in this environment. The
  fix does not repair that; it removes the reason the run depended on it, and
  reports it honestly when it is reached.
- A repair recovers framing, never content. A response truncated *before* the
  fields were emitted cannot be recovered — and because `compare_documents`
  counts an absent prediction as a miss, a partial recovery is scored as the
  partial read it is, not hidden.

**Interview angle.**

- *The strongest sentence in this repository is now a causal chain*: I measured a
  12.2% extraction-failure rate, reproduced it from a captured raw response,
  found it was JSON framing rather than OCR difficulty, fixed the decode path,
  and re-ran the identical measurement to show 0.00% — with both rounds' numbers
  published side by side. "I fixed a bug" is weak; "I built the instrument that
  found the bug and the instrument is what proves the fix" is not.
- *The measurement is the deliverable, twice over.* The second time it caught a
  defect in my own fix that all 291 tests, the whole suite, and a green
  headline number had missed — because the metric that moved (failure rate) was
  not the metric that was broken (field values). Re-measuring after a fix is not
  a formality.
- *Repairing a broken payload is where honesty gets hard.* Every repair rule
  here is written so it can only do one of two things: recover framing, or
  refuse. It never supplies a value, it refuses internally contradictory input
  rather than guessing, and a recovery that produces an empty document is
  rejected as a failure — because the seductive version of this task is the one
  where you make the failure rate go away without making the pipeline better.
- *Variance is a reliability symptom.* Three rounds that agree to four decimals
  are not a coincidence and not a better model — they are what is left when the
  dominant noise source is a crash rather than sampling.

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
- **Section E artifacts follow the same convention.** `benchmark/results/e2e_eval.json`
  exists in the working tree and holds every per-round raw number, but
  `benchmark/results/` is git-ignored by an earlier decision, so the *tracked*
  artifact is `docs/e2e-eval.md` (regenerable from the JSON with
  `python scripts/run_e2e_eval.py --render-only`, zero cost). If the raw JSON
  should be committed, the `.gitignore` line needs revisiting — flagged rather
  than silently overridden. `benchmark/results/e2e_mock.json` is the zero-cost
  harness self-check (`--extractor mock`, two rounds): with a perfect extractor
  it scores 1.000 on every metric, which is the control showing the harness is
  not structurally biased towards the low numbers it reports for the real API.
- **The run's endpoint host is redacted** in the artifact
  (`*.maas.aliyuncs.com (dedicated deployment, host redacted)`). The configured
  base URL is an account-specific MaaS subdomain, and this repository is public;
  `redact_endpoint` keeps the public DashScope host verbatim and reduces a
  dedicated one to its suffix.
- **Cost is list price, not an invoice.** Token counts are measured exactly;
  the CNY figure applies the published `qwen-vl-plus` list price (¥0.8/M input,
  ¥2/M output, 华北2 北京) to them. The account was partly on free-tier quota
  during these runs — the observed 403 on the fallback model says so — so a ¥0
  bill would be a billing artefact, not evidence that extraction is free.
- **Not metered:** the OpenAI SDK's own `max_retries=2` transport retries. A
  retried request is not a separate billed response, so its tokens are not
  visible through the usage hook; the recorded totals are the tokens of the
  responses that were actually returned.
- **The `qwen3.5-ocr` fallback is non-functional in this environment** (403
  `insufficient_quota`), so every primary failure became a hard extraction
  failure. That arguably made the 12.2 % failure rate an upper bound on what the
  configured model pair could achieve, and it was the strongest reason to fix the
  JSON decode path rather than rely on the fallback — which is what section F
  did: the failure rate is now 0 % with the fallback still dead, and the refused
  call is reported as `model unavailable` and accounted for instead of being
  folded into "2 models tried".
