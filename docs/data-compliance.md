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

| Category | Example found | Risk |
|---|---|---|
| Real corporate names | 同济大学, 郑州京东优凯贸易有限公司, 南京苏宁易购电子商务有限公司 … | Real tax entities |
| Real unified social credit codes (税号) | `12100000425006125J`, `91440183797370649Q` … | Real registered tax ids |
| Real personal names (开票人) | 王梅, 刘娅, 徐辰峰, 谢爱迎 … | Personal names |
| Real bank account numbers | 中国农业银行上海翔殷支行 `0332670…` | Financial PII |
| Real order numbers | 订单号 298533157708, JD order ids | Transaction traces |
| Hard-coded API key | `AIza…` (redacted) in `_source/…/code/config.py` | Leaked credential |

**Verdict**: the original samples contain PII and must **not** be distributed.
`_source/` is excluded from the repository (see `.gitignore`) and is only kept
locally as read-only reference material. It is **not** committed.

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

- The repository contains no real personal/company data (verified by
  `scripts/scan_secrets.py` and manual review);
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
