"""Extraction-layer tests: JSON parsing, normalization, mock extractor."""

import json

import pytest

from app.models.invoice import InvoiceDocument
from app.services.extraction.json_utils import extract_json, normalize_raw_invoice
from app.services.extraction.mock_extractor import MockExtractor


class TestExtractJson:
    def test_fenced_json(self):
        text = '说明如下：\n```json\n{"a": 1}\n```\n完毕'
        assert extract_json(text) == {"a": 1}

    def test_bare_json(self):
        assert extract_json('{"invoice_number": "123"}') == {"invoice_number": "123"}

    def test_json_with_surrounding_text(self):
        text = '结果: {"a": [1, 2]} 以上。'
        assert extract_json(text) == {"a": [1, 2]}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            extract_json("完全没有任何 JSON 内容")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            extract_json('{"a": }')

    def test_trailing_commas_repaired(self):
        text = '{"a": 1, "b": [1, 2,], "c": {"d": 3,},}'
        assert extract_json(text) == {"a": 1, "b": [1, 2], "c": {"d": 3}}

    def test_json_with_comment_prose(self):
        text = '这是结果：\n```json\n{"发票号码": "123", "金额": 45.6}\n```\n以上'
        assert extract_json(text) == {"发票号码": "123", "金额": 45.6}


class TestNormalizeRawInvoice:
    def test_chinese_keys_mapped(self):
        raw = {
            "发票号码": "24417000000034170288",
            "开票日期": "2024年07月20日",
            "购买方名称": "同济大学",
            "购买方税号": "12100000425006125J",
            "销售方名称": "示例公司",
            "销售方税号": "91440183797370649Q",
            "项目明细": [{"项目名称": "固态硬盘", "金额": 884.07, "税率": "13%", "税额": 114.93}],
            "金额": "884.07",
            "税额": "114.93",
            "价税合计小写": "￥999.00",
            "校验码": "51191401325570116214",
            "开票人": "王梅",
            "置信度": {"发票号码": 0.99},
        }
        doc = normalize_raw_invoice(raw)
        assert isinstance(doc, InvoiceDocument)
        assert doc.invoice_number == "24417000000034170288"
        assert doc.issue_date.isoformat() == "2024-07-20"
        assert doc.buyer.name == "同济大学"
        assert doc.seller.tax_id == "91440183797370649Q"
        assert doc.amount_including_tax == 999.0
        assert doc.items[0].tax_rate == 13.0
        assert doc.items[0].amount_excluding_tax == 884.07
        assert doc.confidence["invoice_number"] == 0.99

    def test_english_keys_accepted(self):
        raw = {
            "invoice_number": "123",
            "amount_including_tax": "100.50",
            "buyer": {"name": "甲公司", "tax_id": "X"},
            "items": [{"name": "服务", "tax_rate": "6%", "amount_excluding_tax": "94.34"}],
        }
        doc = normalize_raw_invoice(raw)
        assert doc.invoice_number == "123"
        assert doc.amount_including_tax == 100.5
        assert doc.items[0].tax_rate == 6.0

    def test_missing_fields_are_none(self):
        doc = normalize_raw_invoice({})
        assert doc.invoice_number is None
        assert doc.buyer.name == ""
        assert doc.items == []

    def test_confidence_parsed_from_both_naming(self):
        raw = {"置信度": {"发票号码": 0.9}, "confidence": {"issue_date": 0.8}}
        doc = normalize_raw_invoice(raw)
        assert doc.confidence["invoice_number"] == 0.9
        assert doc.confidence["issue_date"] == 0.8

    def test_date_formats(self):
        for date_str, expected in [
            ("2024年07月20日", "2024-07-20"),
            ("2024/07/20", "2024-07-20"),
            ("2024-7-20", "2024-07-20"),
            ("2024-07-20", "2024-07-20"),
        ]:
            assert normalize_raw_invoice({"开票日期": date_str}).issue_date.isoformat() == expected


class TestMockExtractor:
    def test_mock_extractor_returns_document(self):
        extractor = MockExtractor()
        doc = extractor.extract("samples/invoice_001.pdf")
        assert isinstance(doc, InvoiceDocument)
        assert doc.invoice_number is not None

    def test_mock_extractor_uses_ground_truth_by_filename(self, tmp_path):
        gt = {
            "files": {
                "abc.pdf": {"invoice_number": "99999999999999999999", "amount_including_tax": 123.45}
            }
        }
        gt_file = tmp_path / "gt.json"
        gt_file.write_text(json.dumps(gt, ensure_ascii=False), encoding="utf-8")
        extractor = MockExtractor(ground_truth_path=str(gt_file))
        doc = extractor.extract("abc.pdf")
        assert doc.invoice_number == "99999999999999999999"
        assert doc.amount_including_tax == 123.45
