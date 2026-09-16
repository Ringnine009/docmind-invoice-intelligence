# Data Compliance & Privacy

## Why this document exists

This project evolved from a university coursework dataset ("发票智能抽取系统").
That dataset's sample invoices were **real** invoices paid by a real university
and contained **real personal and corporate data**. Per the repository's
privacy rules, none of that data may be committed. This document records the
audit and the mitigation.

## PII audit of the original `_source` samples

The original 30 sample PDFs (`_source/…/invoices/`) and their extraction log
(`_source/…/data/invoices.xlsx`) contain:

| Category | What was found | Risk |
|---|---|---|
| Real corporate names | Several real entities: a university, a trading company, an e-commerce company | Real tax entities |
| Real unified social credit codes (税号) | Multiple 18-character codes, `12…` (public institution) and `91…` (enterprise) prefixes | Real registered tax ids |
| Real personal names (开票人) | Four 2–3 character Chinese personal names | Personal names |
| Real bank account numbers | One account at a real Shanghai bank branch | Financial PII |
| Real order numbers | One 12-digit order id plus JD order ids | Transaction traces |
| Hard-coded API key | `AIza…` (redacted) in `_source/…/code/config.py` | Leaked credential |

The literal values are **not** reproduced here. Quoting real identifiers
inside the document that promises not to publish them is still publishing
them; the audit above records category, shape and count instead.

**Verdict**: the original samples contain PII and must **not** be distributed.
`_source/` is excluded from the repository (see `.gitignore`) and is only kept
locally as read-only reference material. It is **not** committed.

### Remediation: the literals had leaked into the test-suite

The audit below found the PII in `_source/`, but several of those values had
also been copied into the committed test-suite (`tests/conftest.py`,
`tests/test_extraction.py`, `tests/test_graph.py`, `tests/test_schema.py`,
`tests/test_uscc.py`, `tests/test_audit_rules.py`) and shipped on the public
`master` branch — which made the guarantee below false. Every occurrence has
since been replaced with an equivalent synthetic value that keeps the tests
meaningful (valid length, valid GB 32100-2015 check character, wrong-check
variant still repairable) and `tests/test_pii_guard.py` now scans the whole
repository and fails the build if any documented identifier reappears.


## Mitigation: fully synthetic sample set

`scripts/generate_synthetic_invoices.py` generates **30 fabricated** Chinese
e-invoice PDFs plus a machine-readable ground truth:

- Company names are drawn from fictional name pools (no real entities);
- Unified social credit codes are generated with **valid GB 32100-2015 check
  characters** but are not registered to any real entity;
- Issuer names are invented;
- No bank accounts, no real order numbers, no real addresses;
- Amounts, dates and tax rates are random but internally consistent;
- A subset intentionally contains *audit anomalies* (duplicate invoice
  numbers, wrong totals, non-standard tax rates, missing seller tax id,
  self-dealing, future date) so the audit engine can be demonstrated —
  every anomaly is annotated in `benchmark/ground_truth.json`.

The synthetic layout mirrors the visual structure of Chinese electronic VAT
invoices (普通发票) so the vision model is exercised realistically, but every
byte of data in it is fabricated.

## Guarantees

- The repository contains no real personal/company data — enforced by
  `tests/test_pii_guard.py` (which fails on any documented identifier) and by
  `scripts/scan_secrets.py`, plus manual review;
- Every sample value used in the test-suite is synthetic, including the
  buyer/seller parties, tax ids and issuer names in `tests/conftest.py`;
- No API key or credential appears in any committed file
  (`scripts/scan_secrets.py` enforces this);
- The `.env` file (if any) is git-ignored; configuration is provided through
  `.env.example` only;
- All runtime extraction results land in git-ignored `data/`.

## Disclaimer

DocMind is a **demonstration/research project**. It is not affiliated with,
endorsed by, or connected to any of the (fictional) companies appearing in the
synthetic samples. The audit engine flags *potential* inconsistencies and
does **not** constitute a legal or financial audit; always verify against
official tax systems.
