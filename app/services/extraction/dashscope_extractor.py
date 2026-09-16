"""DashScope (Alibaba Cloud Bailian) vision extractor via the OpenAI-compatible
endpoint. Uses ``qwen-vl-plus`` by default with ``qwen3.5-ocr`` as fallback
(qwen3.5-ocr is an OCR-layout model and produced poor structured JSON in our
benchmarks; see docs/benchmark.md)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.config import Settings, get_settings
from app.core.uscc import repair_uscc
from app.models.invoice import InvoiceDocument
from app.services.extraction.base import ExtractionError, Extractor
from app.services.extraction.json_utils import extract_json, normalize_raw_invoice
from app.services.extraction.pdf_utils import pdf_to_png_bytes
from app.services.extraction.qr_utils import decode_qr

_EXTRACTION_PROMPT = """你是一个专业的增值税发票信息抽取引擎。请从发票图片中提取所有字段，只输出一个 JSON 对象，不要输出任何解释或额外文字。

要求：
1. 发票号码必须为 20 位数字。
2. 金额保留两位小数；税率用数字百分比（如 13 表示 13%）。
3. 每个字段都要给出置信度（0 到 1 之间的小数）；图片中不存在或无法识别的字段，值填空字符串或 0，置信度填 0。
4. "项目明细"是数组，包含发票上的所有行；没有明细时返回空数组 []。
5. 不要识别二维码和防伪码内容。

输出 JSON 结构：
{
  "发票类型": "",
  "发票号码": "",
  "开票日期": "",
  "购买方名称": "",
  "购买方税号": "",
  "销售方名称": "",
  "销售方税号": "",
  "项目明细": [
    {"项目名称": "", "规格型号": "", "单位": "", "数量": 0, "单价": 0, "金额": 0, "税率": 0, "税额": 0}
  ],
  "金额": 0,
  "税额": 0,
  "价税合计小写": 0,
  "价税合计大写": "",
  "校验码": "",
  "备注": "",
  "开票人": "",
  "置信度": {
    "发票类型": 0, "发票号码": 0, "开票日期": 0,
    "购买方名称": 0, "购买方税号": 0,
    "销售方名称": 0, "销售方税号": 0,
    "金额": 0, "税额": 0, "价税合计小写": 0,
    "项目明细": 0
  }
}"""


#: Called as ``recorder(model, usage)`` after every billed model response, with
#: usage keys ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``.
#: Purely observational: it cannot change the prompt or the sampling parameters,
#: and it is invoked before JSON parsing so a response that is billed but then
#: discarded still gets accounted for.
UsageRecorder = Callable[[str, Mapping[str, Any]], None]


class DashScopeExtractor(Extractor):
    """Vision extraction backed by a DashScope OpenAI-compatible chat model.

    Uses ``qwen-vl-plus`` by default with ``qwen3.5-ocr`` as fallback. Each
    model call is retried once on flaky responses, and JSON output is repaired
    tolerantly before normalization (see json_utils.extract_json).

    ``usage_recorder`` is an optional observability hook used by the
    end-to-end evaluation to account for tokens (and therefore cost) without
    duplicating the request construction here.
    """

    name = "dashscope"

    def __init__(
        self,
        settings: Settings | None = None,
        usage_recorder: UsageRecorder | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.usage_recorder = usage_recorder
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.settings.dashscope_openai_compat_url,
                api_key=self.settings.require_api_key(),
                timeout=120.0,
                max_retries=2,
            )
        return self._client

    def extract(self, file_path: str | Path) -> InvoiceDocument:
        image_bytes, mime = pdf_to_png_bytes(
            file_path, dpi=self.settings.pdf_render_dpi
        )
        qr_payload = decode_qr(image_bytes)
        errors: list[str] = []
        models = [
            m for m in (self.settings.vision_model_primary, self.settings.vision_model_fallback) if m
        ]
        for model in models:
            for attempt in range(2):  # retry once on flaky responses
                try:
                    raw = self._call_model(model, image_bytes, mime)
                    doc = normalize_raw_invoice(raw)
                    if qr_payload:
                        doc.qr_payload = qr_payload
                    self._repair_tax_ids(doc)
                    return doc
                except (ValueError, ExtractionError) as exc:
                    # parse-level failures are worth one retry
                    if attempt == 0:
                        errors.append(f"{model} (attempt {attempt + 1}): {exc}")
                        continue
                    errors.append(f"{model} (attempt {attempt + 1}): {exc}")
                except Exception as exc:
                    errors.append(f"{model}: {exc}")
                    break
        raise ExtractionError(
            f"all vision models failed ({len(models)} tried): {'; '.join(errors)}"
        )

    @staticmethod
    def _repair_tax_ids(doc: InvoiceDocument) -> None:
        """Fix wrong GB 32100-2015 check characters on extracted tax ids."""
        for side in ("buyer", "seller"):
            party = getattr(doc, side)
            fixed, changed = repair_uscc(party.tax_id)
            if changed:
                party.tax_id = fixed
                doc.corrections[f"{side}.tax_id"] = (
                    "GB 32100-2015 check character repaired"
                )

    def _record_usage(self, model: str, response: Any) -> None:
        """Hand the billed token counts to the recorder, if one is installed."""
        if self.usage_recorder is None:
            return
        usage = getattr(response, "usage", None)
        self.usage_recorder(
            model,
            {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        )

    def _call_model(self, model: str, image_bytes: bytes, mime: str) -> dict:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        response = self._get_client().chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _EXTRACTION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        # Record before parsing: a response that costs tokens and then fails to
        # parse still costs tokens.
        self._record_usage(model, response)
        content = response.choices[0].message.content or ""
        return extract_json(content)
