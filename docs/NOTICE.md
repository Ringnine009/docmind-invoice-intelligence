# NOTICE — credits, data provenance and licensing

DocMind re-implements and significantly extends a university coursework
project ("发票智能抽取系统", invoice intelligent extraction). The original
code, sample invoices and experiment report live under `_source/` on the
author's machine as read-only reference material and are **not** part of this
repository (they contain real PII and a hard-coded API key).

## Reused / inspired components

| Component | Usage | License / credit |
|---|---|---|
| [OpenAI Python SDK](https://github.com/openai/openai-python) | OpenAI-compatible chat completions client for the DashScope vision models | Apache-2.0 |
| [DashScope / Qwen vision models](https://help.aliyun.com/zh/model-studio/) (qwen-vl-plus, qwen3.5-ocr) | Multimodal invoice OCR & structured extraction | Alibaba Cloud Bailian ToS; used via OpenAI-compatible endpoint |
| [FastAPI](https://fastapi.tiangolo.com/) | Web API framework | MIT |
| [Pydantic / pydantic-settings](https://docs.pydantic.dev/) | Typed schemas & env-driven config | MIT |
| [networkx](https://networkx.org/) | Knowledge-graph construction | BSD-3-Clause |
| [PyMuPDF](https://pymupdf.readthedocs.io/) | PDF → image rendering | AGPL-3.0 (usage in a standalone service; see note below) |
| [reportlab](https://www.reportlab.com/) | Synthetic Chinese invoice PDF generation | BSD-style (reportlab license) |
| [qrcode](https://github.com/lincolnloop/python-qrcode) | QR payloads on synthetic invoices | BSD-3-Clause |
| [vis-network](https://github.com/visjs/vis-network) | Frontend knowledge-graph visualization | Apache-2.0 / MIT (visjs) |
| [React](https://react.dev/) + [Vite](https://vitejs.dev/) + TypeScript | Frontend dashboard | MIT |

> **Note on PyMuPDF (AGPL-3.0):** the AGPL applies to code that *links*
> against PyMuPDF. DocMind is offered under MIT, but if you redistribute or
> host this project as a service, review whether the AGPL obligations of the
> bundled PyMuPDF dependency apply to your use case, and swap the PDF→image
> renderer (e.g. pdf2image/poppler) if needed. The synthetic sample PDFs in
> `samples/` were produced by reportlab and are unaffected.

## Data

- `samples/*.pdf` and `benchmark/ground_truth.json` are **fully synthetic**
  (see [docs/data-compliance.md](docs/data-compliance.md)); no real personal
  or corporate data is included.
- Ground-truth field schema (English keys) is defined by DocMind itself.

## Trademarks

All company/product names appearing in the synthetic samples are fictional;
any resemblance to real entities is coincidental. "Qwen" and "DashScope" are
trademarks of their respective owners.

## License

DocMind is released under the MIT License — see [LICENSE](LICENSE).
