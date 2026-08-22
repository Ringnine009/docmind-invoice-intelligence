"""GB 32100-2015 USCC check/repair tests."""

from app.core.uscc import repair_uscc, uscc_check_char, uscc_checksum_ok

# Valid real-world codes (format verified against the original dataset).
VALID = [
    "12100000425006125J",
    "91440183797370649Q",
    "91310107MA1G1C8Q5W",
    "91310000MA1FR8MB1W",
    "91110302562134916R",
]


class TestChecksum:
    def test_valid_codes_pass(self):
        for code in VALID:
            assert uscc_checksum_ok(code)

    def test_wrong_check_char_fails(self):
        assert not uscc_checksum_ok("12100000425006125K")

    def test_bad_length_fails(self):
        assert not uscc_checksum_ok("123")
        assert not uscc_checksum_ok("12100000425006125JK")

    def test_check_char_is_deterministic(self):
        for code in VALID:
            assert uscc_check_char(code[:17]) == code[17]


class TestRepair:
    def test_repairs_wrong_check_char(self):
        bad = "12100000425006125K"  # valid body, wrong check char
        fixed, changed = repair_uscc(bad)
        assert changed is True
        assert uscc_checksum_ok(fixed)
        assert fixed == "12100000425006125J"

    def test_valid_code_untouched(self):
        fixed, changed = repair_uscc("91440183797370649Q")
        assert changed is False
        assert fixed == "91440183797370649Q"

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
