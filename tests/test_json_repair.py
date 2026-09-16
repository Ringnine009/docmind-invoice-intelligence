"""Bad-JSON recovery: the defect that dominated the end-to-end evaluation.

`docs/e2e-eval.md` measured 11 extraction failures in 90 attempts (12.2%), every
one of them the same defect: *"malformed JSON in model response: Expecting ','
delimiter"* from the primary model on both attempts, with the configured
fallback rejected by the endpoint (403 insufficient_quota).

The three files under ``tests/fixtures/model_responses/`` are **real responses**
recorded from ``qwen-vl-plus`` on `samples/invoice_015.pdf` and
`samples/invoice_012.pdf` through the project's own prompt, DPI and sampling
parameters (see ``.probe/capture_raw.py`` at capture time). They are synthetic
invoices — no real entity appears in them.

Two distinct shapes are in those recordings, and both were fatal:

1. **unclosed outer object** — the whole document body is emitted, then the
   model rambles whitespace instead of writing the final ``}``;
2. **repeated tail** — the model re-emits members it already emitted and is cut
   off mid-string, so ``text[first "{" : last "}"]`` (the previous decode path)
   glues object fragments together into something no parser accepts.

Shapes 1 and 2 both mean the *document body was there*; only the framing was
broken. What must not happen is the opposite mistake: repairing a response that
carries no invoice at all into a "successfully extracted" empty document, which
would make the failure rate look better without a document being read.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.extraction import dashscope_extractor as ds
from app.services.extraction.base import ExtractionError
from app.services.extraction.json_utils import extract_json, normalize_raw_invoice

FIXTURES = Path(__file__).parent / "fixtures" / "model_responses"

#: Recorded response -> the fields it does contain (read off the payload by the
#: capture script, not from ground truth, so the assertion is about *decoding*
#: the model's own output rather than about OCR accuracy).
RECORDED = {
    "unclosed_object_whitespace_tail.txt": {
        "发票号码": "24417000000029990002",
        "价税合计小写": 1629.69,
    },
    "truncated_repeated_tail.txt": {
        "发票号码": "24417000000029990002",
        "价税合计小写": 1629.69,
    },
    "repeated_tail_cut_mid_string.txt": {
        "发票号码": "24001299016272046537",
        "价税合计小写": 40.29,
    },
}

#: Five malformed shapes. The first three are minimised versions of the recorded
#: defect, the last two are the neighbouring shapes a repair ladder must also
#: handle (a second object glued on, and a fence plus trailing commas on top of
#: a truncation).
SHAPES = {
    "unclosed-outer-object": (
        '{"发票号码": "12345678901234567890", "价税合计小写": 199.0'
    ),
    "repeated-tail-never-closed": (
        '{"发票号码": "12345678901234567890",\n'
        '  "价税合计小写": 199.0\n'
        '  ,\n'
        '  "价税合计小写": 199.0'
    ),
    "cut-mid-string": (
        '{"发票号码": "12345678901234567890", "备注": "合成样例数据，仅供演示'
    ),
    "second-object-appended": (
        '{"发票号码": "12345678901234567890"}\n'
        '{"发票号码": "99999999999999999999"}'
    ),
    "fenced-trailing-commas-truncated": (
        "```json\n"
        '{"发票号码": "12345678901234567890",'
        ' "项目明细": [{"项目名称": "服务",},],\n'
        "```"
    ),
}


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- response fakes (no network, no PDF rendering) --------------------------


class _HttpError(Exception):
    """Stands in for the openai client's APIStatusError on a 403."""


class _Completions:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake client ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
            usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=50, total_tokens=150
            ),
        )


class _Client:
    def __init__(self, script: list) -> None:
        self.completions = _Completions(script)
        self.chat = SimpleNamespace(completions=self.completions)


def build_extractor(script: list):
    """A DashScopeExtractor wired to a scripted client instead of the network."""
    settings = Settings(
        dashscope_api_key="test-key",
        vision_model_primary="qwen-vl-plus",
        vision_model_fallback="qwen3.5-ocr",
    )
    extractor = ds.DashScopeExtractor(settings)
    client = _Client(script)
    extractor._client = client
    return extractor, client


@pytest.fixture(autouse=True)
def _no_pdf_work(monkeypatch):
    """Skip rendering and QR decoding: this file tests the decode path."""
    monkeypatch.setattr(
        ds, "pdf_to_png_bytes", lambda path, dpi=None: (b"png-bytes", "image/png")
    )
    monkeypatch.setattr(ds, "decode_qr", lambda image_bytes: None)


def extract_error(extractor, path="samples/invoice_015.pdf") -> str:
    with pytest.raises(ExtractionError) as excinfo:
        extractor.extract(path)
    return str(excinfo.value)


# --- 1. the recorded failures must decode -----------------------------------


class TestRecordedModelResponses:
    @pytest.mark.parametrize("name", sorted(RECORDED))
    def test_the_real_response_now_decodes(self, name):
        parsed = extract_json(read_fixture(name))
        expected = RECORDED[name]
        assert parsed.get("发票号码") == expected["发票号码"]
        assert parsed.get("价税合计小写") == expected["价税合计小写"]

    @pytest.mark.parametrize("name", sorted(RECORDED))
    def test_the_document_normalizes_from_the_repaired_response(self, name):
        doc = normalize_raw_invoice(extract_json(read_fixture(name)))
        assert doc.invoice_number == RECORDED[name]["发票号码"]
        assert doc.amount_including_tax == RECORDED[name]["价税合计小写"]

    def test_the_strict_slice_really_was_the_problem(self):
        """Pin the mechanism: the old first-`{`/last-`}` slice is not valid JSON.

        Without this, a future change that removes the repair could still pass
        the tests above by accident (e.g. if the fixture were replaced).
        """
        raw = read_fixture("unclosed_object_whitespace_tail.txt")
        start, end = raw.find("{"), raw.rfind("}")
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw[start : end + 1])


# --- 2. the shapes ----------------------------------------------------------


class TestMalformedShapes:
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_every_shape_decodes(self, shape):
        parsed = extract_json(SHAPES[shape])
        assert isinstance(parsed, dict)
        assert parsed.get("发票号码")

    def test_repeated_tail_keeps_the_document_values(self):
        parsed = extract_json(SHAPES["repeated-tail-never-closed"])
        assert parsed == {"发票号码": "12345678901234567890", "价税合计小写": 199.0}

    def test_appended_second_object_keeps_the_first_document(self):
        """The first complete object is the invoice; the echo is not data."""
        parsed = extract_json(SHAPES["second-object-appended"])
        assert parsed["发票号码"] == "12345678901234567890"

    def test_fenced_trailing_comma_truncation_decodes(self):
        parsed = extract_json(SHAPES["fenced-trailing-commas-truncated"])
        assert parsed["项目明细"] == [{"项目名称": "服务"}]

    def test_the_unterminated_tail_is_dropped_never_guessed(self):
        """The repair closes brackets and drops the unterminated tail.

        ``价税合计小写`` had no value at all, so it is *absent* from the result
        rather than invented as 0 — a fabricated number would be far worse than
        a missing one, because the audit engine reads 0 as "the invoice says 0".
        """
        parsed = extract_json('{"发票号码": "12345678901234567890", "价税合计小写":')
        assert parsed == {"发票号码": "12345678901234567890"}

    def test_no_complete_member_is_ever_discarded(self):
        parsed = extract_json(
            '{"发票号码": "1", "价税合计小写": 199.0, "备注": "截断在这'
        )
        assert parsed["价税合计小写"] == 199.0
        assert "备注" not in parsed

    def test_a_contradictory_payload_is_not_guessed_at(self):
        """Structurally broken *inside* is not truncation — refuse to repair."""
        with pytest.raises(ValueError):
            extract_json('{"发票号码": "123", "价税合计小写": }')


# --- 3. good responses must come out untouched ------------------------------


class TestValidResponsesUnaffected:
    CASES = {
        "plain": '{"发票号码": "12345678901234567890", "价税合计小写": 199.0}',
        "nested": (
            '{"发票号码": "1", "项目明细": [{"项目名称": "服务", "数量": 2,'
            ' "单价": 1.5}], "置信度": {"发票号码": 1.0}, "备注": null}'
        ),
        "braces-and-quotes-in-strings": (
            '{"备注": "含 } 和 { 与 \\" 引号 }}}", "发票号码": "1"}'
        ),
        "unicode-escapes": '{"发票号码": "1", "备注": "\\u4e2d\\u6587"}',
        "fenced": '说明：\n```json\n{"发票号码": "1"}\n```\n以上',
        "surrounded": '结果: {"发票号码": "1"} 以上。',
    }

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_steering_does_not_change_valid_data(self, case):
        text = self.CASES[case]
        parsed = extract_json(text)
        if case in {"fenced", "surrounded"}:
            start, end = text.find("{"), text.rfind("}")
            assert parsed == json.loads(text[start : end + 1])
        else:
            assert parsed == json.loads(text)

    def test_a_valid_response_is_reported_as_verbatim(self):
        from app.services.extraction.json_utils import extract_json_verbose

        good = '{"发票号码": "1"}'
        parsed, strategy = extract_json_verbose(good)
        assert parsed == {"发票号码": "1"}
        assert strategy == "verbatim", (
            "a well-formed response must not be routed through a repair path"
        )

    def test_a_repaired_response_names_its_strategy(self):
        from app.services.extraction.json_utils import extract_json_verbose

        parsed, strategy = extract_json_verbose(SHAPES["unclosed-outer-object"])
        assert parsed["发票号码"] == "12345678901234567890"
        assert strategy != "verbatim"

    def test_still_rejects_what_was_always_rejected(self):
        for text in ("完全没有任何 JSON 内容", "", "   ", '{"a": }'):
            with pytest.raises(ValueError):
                extract_json(text)


class TestRepeatedPlaceholderSkeleton:
    """A repeated key must not let the model's own echo overwrite its reading.

    Recorded response (``invoice_006.pdf``, captured while investigating a
    predicted ``金额`` of 0.0 with model-reported confidence 1.0): the model
    emits a complete, correct document and then degenerates into repeating the
    *line-item skeleton* — ``"项目名称": "", "数量": 0, "单价": 0, "金额": 0`` —
    as **top-level** keys, out of the array it belongs to, before being cut off.

    Python's ``json`` keeps the *last* occurrence of a duplicated key, so the
    placeholder overwrote the number the model had actually read. The
    repetition is degeneration, not a correction, so the first occurrence is
    the reading — and getting this wrong turns a correct read into a confident
    zero, which is worse than the failure the repair was written to fix.
    """

    def test_the_echo_does_not_overwrite_the_read_values(self):
        parsed = extract_json(read_fixture("repeated_skeleton_zeros.txt"))
        assert parsed["金额"] == 70.3
        assert parsed["税额"] == 4.22
        assert parsed["价税合计小写"] == 74.52
        assert parsed["发票号码"] == "24000600978820812191"

    def test_the_document_keeps_the_values_the_model_read(self):
        doc = normalize_raw_invoice(
            extract_json(read_fixture("repeated_skeleton_zeros.txt"))
        )
        assert doc.amount_excluding_tax == 70.3
        assert doc.tax_amount == 4.22
        assert doc.amount_including_tax == 74.52
        assert len(doc.items) == 1

    def test_first_occurrence_wins_after_a_truncation_repair(self):
        assert extract_json('{"a": 1, "b": 2, "a": 99, "b": 98') == {"a": 1, "b": 2}

    def test_first_occurrence_wins_in_a_well_formed_response_too(self):
        """Duplicate keys are a defect either way — the prompt forbids them."""
        assert extract_json('{"金额": 70.3, "金额": 0}') == {"金额": 70.3}

    def test_the_repair_strategy_is_named_for_this_shape(self):
        from app.services.extraction.json_utils import extract_json_verbose

        parsed, strategy = extract_json_verbose(
            read_fixture("repeated_skeleton_zeros.txt")
        )
        assert parsed["金额"] == 70.3
        assert strategy != "verbatim"


# --- 4. the extractor: corrective retry, bounded cost ----------------------


class TestCorrectiveRetry:
    #: A response the repair ladder must *not* touch: the value for
    #: ``价税合计小写`` is missing entirely, so accepting it would mean
    #: inventing a number. This is what still needs the corrective re-ask.
    UNREPAIRABLE = '{"发票号码": "24417000000029990002", "价税合计小写": }'

    def test_bad_json_is_retried_with_the_error_fed_back(self):
        good = '{"发票号码": "24417000000029990002", "价税合计小写": 1629.69}'
        extractor, client = build_extractor([self.UNREPAIRABLE, good])

        doc = extractor.extract("samples/invoice_015.pdf")

        assert doc.invoice_number == "24417000000029990002"
        assert doc.amount_including_tax == 1629.69
        assert len(client.completions.calls) == 2, "one retry, not a loop"

        retry = client.completions.calls[1]["messages"]
        assert [m["role"] for m in retry] == ["user", "assistant", "user"]
        assert retry[1]["content"] == self.UNREPAIRABLE, (
            "the model gets its own output back to correct"
        )
        assert any(
            part.get("type") == "image_url" for part in retry[0]["content"]
        ), "the retry must still see the invoice image"
        assert "malformed JSON in model response" in retry[2]["content"], (
            "the parser's complaint is quoted back, not just re-asked"
        )
        assert "JSON" in retry[2]["content"]

    def test_a_repaired_first_response_is_recorded_as_a_correction(self):
        bad = read_fixture("truncated_repeated_tail.txt")
        extractor, client = build_extractor([bad])
        doc = extractor.extract("samples/invoice_015.pdf")
        assert len(client.completions.calls) == 1
        assert "json" in doc.corrections, doc.corrections

    def test_a_valid_response_costs_exactly_one_call(self):
        good = json.dumps(
            {
                "发票号码": "24417000000029990002",
                "价税合计小写": 1629.69,
                "项目明细": [{"项目名称": "会议注册费", "金额": 1537.44}],
            },
            ensure_ascii=False,
        )
        extractor, client = build_extractor([good])
        doc = extractor.extract("samples/invoice_015.pdf")
        assert doc.invoice_number == "24417000000029990002"
        assert len(client.completions.calls) == 1
        assert "json" not in doc.corrections

    def test_the_retry_is_bounded_per_model(self):
        """Two calls per model, not a loop — the budget is a real constraint."""
        extractor, client = build_extractor([self.UNREPAIRABLE] * 4)
        extract_error(extractor)
        assert len(client.completions.calls) == 4

    def test_every_configured_model_is_tried_in_order(self):
        extractor, client = build_extractor([self.UNREPAIRABLE] * 4)
        message = extract_error(extractor)
        assert "qwen-vl-plus" in message and "qwen3.5-ocr" in message
        assert [call["model"] for call in client.completions.calls] == [
            "qwen-vl-plus",
            "qwen-vl-plus",
            "qwen3.5-ocr",
            "qwen3.5-ocr",
        ]

    def test_a_working_fallback_is_only_charged_when_it_is_used(self):
        """The fallback must not be called for a document the primary read."""
        good = '{"发票号码": "24417000000029990002", "价税合计小写": 1629.69}'
        extractor, client = build_extractor([good, good])
        extractor.extract("samples/invoice_015.pdf")
        assert [call["model"] for call in client.completions.calls] == ["qwen-vl-plus"]

    def test_the_fallback_is_used_and_recorded_when_the_primary_fails(self):
        good = '{"发票号码": "24417000000029990002", "价税合计小写": 1629.69}'
        extractor, client = build_extractor([self.UNREPAIRABLE] * 2 + [good])
        doc = extractor.extract("samples/invoice_015.pdf")
        assert doc.invoice_number == "24417000000029990002"
        assert "qwen3.5-ocr" in doc.corrections["vision_model"]


# --- 5. honest failure semantics -------------------------------------------


class TestHonestFailureSemantics:
    BAD = TestCorrectiveRetry.UNREPAIRABLE

    def test_an_unusable_answer_is_not_reported_as_a_missing_model(self):
        extractor, _ = build_extractor(
            [self.BAD, self.BAD, _HttpError("Error code: 403 - insufficient_quota")]
        )
        message = extract_error(extractor)
        assert "qwen-vl-plus: 2 response(s), none usable" in message
        assert "qwen3.5-ocr: model unavailable" in message
        assert "403" in message

    def test_a_refused_fallback_is_not_counted_as_a_read_attempt(self):
        """`N models tried` used to imply the fallback had read the document."""
        extractor, _ = build_extractor(
            [self.BAD, self.BAD, _HttpError("Error code: 403 - insufficient_quota")]
        )
        message = extract_error(extractor)
        assert "no document read" in message
        assert "2 tried" not in message

    def test_a_response_with_no_invoice_fields_is_not_a_success(self):
        """Closing a brace must never turn 'nothing was read' into a document."""
        extractor, client = build_extractor(["{}", "{}", "{}", "{}"])
        message = extract_error(extractor)
        assert "no invoice fields" in message
        assert len(client.completions.calls) == 4  # both models, retry included

    def test_unavailable_calls_are_visible_to_the_usage_recorder(self):
        recorded: list[tuple] = []

        def recorder(model, usage):
            recorded.append((model, dict(usage)))

        settings = Settings(
            dashscope_api_key="test-key",
            vision_model_primary="qwen-vl-plus",
            vision_model_fallback="qwen3.5-ocr",
        )
        extractor = ds.DashScopeExtractor(settings, usage_recorder=recorder)
        extractor._client = _Client(
            [self.BAD, self.BAD, _HttpError("Error code: 403 - insufficient_quota")]
        )
        extract_error(extractor)
        fallback = [entry for entry in recorded if entry[0] == "qwen3.5-ocr"]
        assert fallback, "the refused fallback call must be accounted for"
        assert fallback[0][1].get("ok") is False
        assert "403" in str(fallback[0][1].get("error"))


# --- 6. the content gate itself --------------------------------------------


class TestAuditableContentGate:
    def test_a_thin_payload_is_not_auditable(self):
        from app.services.extraction.json_utils import has_auditable_content

        assert has_auditable_content(normalize_raw_invoice({})) is False
        assert (
            has_auditable_content(
                normalize_raw_invoice({"置信度": {"发票号码": 1.0}, "发票类型": "电子发票"})
            )
            is False
        )

    def test_a_real_payload_is_auditable(self):
        from app.services.extraction.json_utils import has_auditable_content

        for raw in (
            {"发票号码": "1"},
            {"价税合计小写": 1.0},
            {"项目明细": [{"项目名称": "服务"}]},
            {"销售方税号": "91310000000000000U"},
        ):
            assert has_auditable_content(normalize_raw_invoice(raw)) is True, raw
