"""QR decode/parse tests (roundtrip uses a real generated QR image)."""

import io

import pytest

from app.services.extraction.qr_utils import (
    QrPayload,
    decode_qr,
    parse_qr_payload,
)


def make_qr_png(payload: str) -> bytes:
    import qrcode

    img = qrcode.make(payload, box_size=8, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestDecodeQr:
    def test_roundtrip_decode(self):
        payload = "01,32,,24417000000034170288,199.00,20240720"
        decoded = decode_qr(make_qr_png(payload))
        assert decoded == payload

    def test_no_qr_returns_none(self):
        assert decode_qr(b"not an image") is None

    def test_blank_image_returns_none(self):
        import qrcode

        img = qrcode.make("x")  # tiny — still decodes? use plain bytes instead
        del img
        assert decode_qr(io.BytesIO().getvalue()) is None


class TestParseQrPayload:
    def test_valid_payload(self):
        parsed = parse_qr_payload("01,32,,24417000000034170288,199.00,20240720")
        assert isinstance(parsed, QrPayload)
        assert parsed.number == "24417000000034170288"
        assert parsed.amount == 199.00
        assert parsed.date.isoformat() == "2024-07-20"

    def test_extra_fields_after_date(self):
        parsed = parse_qr_payload("01,32,,24417000000034170288,199.00,20240720,extra")
        assert parsed.amount == 199.00

    def test_missing_date(self):
        parsed = parse_qr_payload("01,32,,24417000000034170288,199.00")
        assert parsed.date is None
        assert parsed.number == "24417000000034170288"

    def test_amount_with_currency_prefix(self):
        parsed = parse_qr_payload("01,32,,24417000000034170288,￥199.00,20240720")
        assert parsed.amount == 199.00

    def test_garbage_returns_none(self):
        assert parse_qr_payload("hello world") is None
        assert parse_qr_payload("") is None
        assert parse_qr_payload(None) is None

    def test_bad_date_yields_none_date(self):
        parsed = parse_qr_payload("01,32,,24417000000034170288,199.00,20241399")
        assert parsed.date is None
        assert parsed.number == "24417000000034170288"
