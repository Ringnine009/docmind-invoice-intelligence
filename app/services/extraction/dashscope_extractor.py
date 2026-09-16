"""DashScope (Alibaba Cloud Bailian) vision extractor via the OpenAI-compatible
endpoint. Uses ``qwen-vl-plus`` by default with ``qwen3.5-ocr`` as fallback
(qwen3.5-ocr is an OCR-layout model and produced poor structured JSON in our
benchmarks; see docs/benchmark.md).

A vision model is not a function: it can answer with something that is not a
usable invoice. In the end-to-end evaluation 12.2% of attempts came back
unusable, every one of them the same defect (see docs/e2e-eval.md). Three
layers deal with that, cheapest first:

1. :func:`app.services.extraction.json_utils.extract_json` repairs the
   *framing* of a response (unclosed object, repeated tail, trailing commas,
   code fences) without inventing a single value;
2. a response that is still unusable — or that parses into a document with no
   invoice fields at all — gets one **corrective re-ask**: the model's own
   output goes back into the conversation as its previous turn, followed by the
   parser's complaint. That is a different question from the one that produced
   the defect, which is why a plain retry at temperature 0 did not help in the
   measured run: it reproduced the same bytes;
3. if a model produces nothing usable, the next configured model is tried; a
   model that refuses the request (quota, auth) is reported as *unavailable*
   rather than as a document it read and failed.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.config import Settings, get_settings
from app.core.uscc import repair_uscc
from app.models.invoice import InvoiceDocument
from app.services.extraction.base import (
    ExtractionError,
    Extractor,
    ModelUnavailableError,
    UnusableResponseError,
)
from app.services.extraction.json_utils import (
    extract_json_verbose,
    has_auditable_content,
    normalize_raw_invoice,
)
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

#: The corrective turn. It quotes the parser's complaint and restates only the
#: framing rules — the field list is in the original prompt, which is still in
#: the conversation, so repeating it would only add tokens.
_REPAIR_TEMPLATE = """你上一次的回复无法作为发票 JSON 使用，解析器的报错是：

{error}

请重新阅读这张发票图片，只输出一个完整、合法的 JSON 对象：
1. 第一个字符必须是 {{，最后一个字符必须是 }}，所有括号和引号都必须闭合；
2. 字段之间用英文逗号分隔，不要重复输出同一个键，结尾不要有多余字符或空行；
3. 只输出这一个 JSON 对象本身，不要解释、不要 markdown 代码块、不要附加任何内容；
4. 字段名称、结构与第一次要求完全一致。"""

#: Sampling temperature for the corrective re-ask. The first read stays at 0.0
#: (reproducible, and comparable with the recorded runs); a *deterministic*
#: re-ask is what reproduced the identical defect in the measured run, so the
#: correction is deliberately sampled.
_REPAIR_TEMPERATURE = 0.2

#: How much of the failing output to quote back around the error position.
_REPAIR_EXCERPT_CHARS = 160

#: Called as ``recorder(model, usage)`` after every model call, with token keys
#: ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``. A call that was
#: refused before it billed anything is reported too — zero tokens plus
#: ``ok=False`` and an ``error`` — because otherwise a dead fallback model is
#: invisible in the cost and failure totals. Observational only: it cannot
#: change the prompt or the sampling parameters.
UsageRecorder = Callable[[str, Mapping[str, Any]], None]

#: Reported when a response parsed into something with no invoice in it.
_NO_FIELDS_MESSAGE = (
    "the response parsed but carried no invoice fields "
    "(no number, amounts, party or line items)"
)


@dataclass
class _ModelAttempt:
    """What one configured model did, in the terms the error message needs."""

    model: str
    calls: int = 0
    doc: InvoiceDocument | None = None
    strategy: str = "verbatim"
    unavailable: str | None = None
    unusable: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.unavailable is not None:
            return (
                f"{self.model}: model unavailable after {self.calls} call(s) "
                f"(request refused, no document read) — {self.unavailable}"
            )
        return (
            f"{self.model}: {self.calls} response(s), none usable — "
            + "; ".join(self.unusable)
        )


def _excerpt_around(text: str, error: str) -> str:
    """The part of ``text`` the parser choked on, or "" for a short response."""
    if len(text) <= _REPAIR_EXCERPT_CHARS * 3:
        return ""
    match = re.search(r"char (\d+)", error)
    position = int(match.group(1)) if match else len(text)
    lo = max(0, position - _REPAIR_EXCERPT_CHARS)
    hi = min(len(text), position + _REPAIR_EXCERPT_CHARS)
    return text[lo:hi].strip()


def _repair_prompt(error: str, raw: str) -> str:
    prompt = _REPAIR_TEMPLATE.format(error=error)
    excerpt = _excerpt_around(raw, error)
    if excerpt:
        prompt += f"\n\n（出错位置附近的原始输出，仅供定位，不要照抄）\n{excerpt}"
    return prompt


def _response_text(exc: BaseException, fallback: str) -> str:
    """The response an error is about, from whichever attribute carries it.

    A parse failure happens inside the call that produced the text, so the text
    never reaches the caller as a return value — it rides on the exception.
    """
    return getattr(exc, "raw_text", None) or getattr(exc, "raw", None) or fallback


class DashScopeExtractor(Extractor):
    """Vision extraction backed by a DashScope OpenAI-compatible chat model.

    Uses ``qwen-vl-plus`` by default with ``qwen3.5-ocr`` as fallback. Each
    model gets at most :data:`MAX_CALLS_PER_MODEL` calls — a read, and (only if
    the read was unusable) a corrective re-read. JSON framing is repaired
    tolerantly before normalization (see ``json_utils.extract_json``).

    ``usage_recorder`` is an optional observability hook used by the
    end-to-end evaluation to account for tokens (and therefore cost) without
    duplicating the request construction here.
    """

    name = "dashscope"

    #: A read plus one corrective re-read. Bounded so a model that cannot answer
    #: cannot spend the budget trying.
    MAX_CALLS_PER_MODEL = 2

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
        models = [
            m
            for m in (
                self.settings.vision_model_primary,
                self.settings.vision_model_fallback,
            )
            if m
        ]
        attempts: list[_ModelAttempt] = []
        for model in models:
            attempt = self._read_with_model(model, image_bytes, mime)
            attempts.append(attempt)
            # Stop at the first model that read the invoice: the fallback is a
            # fallback, not a second opinion, and calling it anyway would double
            # the cost of every successful document.
            if attempt.doc is None:
                continue
            doc = attempt.doc
            if qr_payload:
                doc.qr_payload = qr_payload
            self._repair_tax_ids(doc)
            if attempt.strategy != "verbatim":
                doc.corrections["json"] = attempt.strategy
            if models and attempt.model != models[0]:
                doc.corrections["vision_model"] = (
                    f"{attempt.model} (fallback: {models[0]} produced no "
                    "usable document)"
                )
            return doc
        raise ExtractionError(
            f"all vision models failed ({len(models)} configured): "
            + "; ".join(attempt.describe() for attempt in attempts)
        )

    # -- one model ----------------------------------------------------------

    def _read_with_model(
        self, model: str, image_bytes: bytes, mime: str
    ) -> _ModelAttempt:
        """Read one invoice with ``model``, re-asking once with the defect quoted."""
        attempt = _ModelAttempt(model=model)
        messages = [self._user_message(image_bytes, mime)]
        while attempt.calls < self.MAX_CALLS_PER_MODEL:
            attempt.calls += 1
            raw = ""
            try:
                raw, parsed, strategy = self._read_once(
                    model,
                    messages,
                    temperature=(
                        0.0 if attempt.calls == 1 else _REPAIR_TEMPERATURE
                    ),
                )
                doc = normalize_raw_invoice(parsed)
                if not has_auditable_content(doc):
                    raise UnusableResponseError(_NO_FIELDS_MESSAGE, raw_text=raw)
            except ModelUnavailableError as exc:
                # Nothing to correct and no reason to re-ask: no document was read.
                attempt.unavailable = str(exc)
                return attempt
            except (UnusableResponseError, ValueError) as exc:
                attempt.unusable.append(f"call {attempt.calls}: {exc}")
                if attempt.calls >= self.MAX_CALLS_PER_MODEL:
                    break
                messages = self._repair_messages(
                    image_bytes, mime, _response_text(exc, raw), exc
                )
                continue
            attempt.doc = doc
            attempt.strategy = strategy
            return attempt
        return attempt

    def _user_message(self, image_bytes: bytes, mime: str) -> dict:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": _EXTRACTION_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        }

    def _repair_messages(
        self, image_bytes: bytes, mime: str, raw: str, exc: Exception
    ) -> list[dict]:
        """The conversation for the corrective re-read.

        The model's own output is replayed as its previous turn — that is the
        payload it has to fix, and re-sending it costs fewer tokens than
        quoting it again in the instruction.
        """
        messages = [self._user_message(image_bytes, mime)]
        if raw:
            messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": _repair_prompt(str(exc), raw)})
        return messages

    # -- one billed call ----------------------------------------------------

    def _read_once(
        self, model: str, messages: list[dict], temperature: float
    ) -> tuple[str, dict, str]:
        """One request: raw text, parsed payload, and how it was parsed."""
        raw = self._request(model, messages, temperature)
        parsed, strategy = extract_json_verbose(raw)
        return raw, parsed, strategy

    def _call_model(
        self,
        model: str,
        image_bytes: bytes,
        mime: str,
        messages: list[dict] | None = None,
    ) -> dict:
        """Single read of one invoice: one request, parsed.

        Kept as the plain entry point the usage-accounting tests drive; the
        retry logic lives in :meth:`_read_with_model`.
        """
        _, parsed, _ = self._read_once(
            model, messages or [self._user_message(image_bytes, mime)], 0.0
        )
        return parsed

    def _request(self, model: str, messages: list[dict], temperature: float) -> str:
        """One billed request; the raw content of the first choice."""
        try:
            response = self._get_client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 — classified, never swallowed
            self._record_unavailable(model, exc)
            raise ModelUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        # Record before parsing: a response that costs tokens and then fails to
        # parse still costs tokens.
        self._record_usage(model, response)
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        return getattr(message, "content", None) or ""

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
        """Hand the billed token counts to the recorder, if one is installed.

        A billed call reports exactly the three token keys; a call that never
        billed is reported by :meth:`_record_unavailable` with ``ok=False``.
        """
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

    def _record_unavailable(self, model: str, exc: BaseException) -> None:
        """Account for a call that was refused before it billed anything."""
        if self.usage_recorder is None:
            return
        self.usage_recorder(
            model,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            },
        )
