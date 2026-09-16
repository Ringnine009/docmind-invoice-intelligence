"""GB 32100-2015 USCC check/repair tests."""

from app.core.uscc import repair_uscc, uscc_check_char, uscc_checksum_ok

from conftest import BUYER_TAX_ID, BUYER_TAX_ID_BAD_CHECK

# Synthetic 18-character codes. The registration-authority digits and the
# region codes are shaped like real unified social credit codes, but the
# 9-character organisation-code section is all zeros, so none of these is
# registered to any entity; only the check character is real maths.
VALID = [
    BUYER_TAX_ID,  # 91310000000000000U
    "91440000000000000Y",
    "91110000000000000E",
    "913200000000000000",
    "915100000000000009",
]


class TestChecksum:
    def test_valid_codes_pass(self):
        for code in VALID:
            assert uscc_checksum_ok(code)

    def test_wrong_check_char_fails(self):
        assert not uscc_checksum_ok(BUYER_TAX_ID_BAD_CHECK)

    def test_bad_length_fails(self):
        assert not uscc_checksum_ok("123")
        assert not uscc_checksum_ok(BUYER_TAX_ID + "K")

    def test_check_char_is_deterministic(self):
        for code in VALID:
            assert uscc_check_char(code[:17]) == code[17]


class TestRepair:
    def test_repairs_wrong_check_char(self):
        bad = BUYER_TAX_ID_BAD_CHECK  # valid body, wrong check char
        fixed, changed = repair_uscc(bad)
        assert changed is True
        assert uscc_checksum_ok(fixed)
        assert fixed == BUYER_TAX_ID

    def test_valid_code_untouched(self):
        fixed, changed = repair_uscc("91440000000000000Y")
        assert changed is False
        assert fixed == "91440000000000000Y"

    def test_none_untouched(self):
        assert repair_uscc(None) == (None, False)

    def test_short_code_untouched(self):
        fixed, changed = repair_uscc("12345")
        assert changed is False

    def test_invalid_chars_in_body_untouched(self):
        # OCR mangled the body itself — cannot repair.
        fixed, changed = repair_uscc("9132000039D5E!YPW7K")
        assert changed is False
        assert fixed == "9132000039D5E!YPW7K"

    def test_19_char_untouched(self):
        fixed, changed = repair_uscc("1210Q0000425006125JK")
        assert changed is False
