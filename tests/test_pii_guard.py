"""Compliance guard: no documented PII literal may appear anywhere in the repo.

`docs/data-compliance.md` audits the original coursework dataset and lists the
real personal/corporate identifiers that must **never** be committed. Those
values were nonetheless hard-coded in the test-suite and shipped on the public
`master` branch, which made the document's guarantee false. This guard makes
the guarantee executable.

The needles are base64-encoded on purpose: a scanner that contains the
literals it looks for would flag itself. Decode them with
``python -c "import base64,base64.b64decode(...)"`` if you need to read them.
"""

import base64

import pytest

from app.core.config import find_repo_root

# 15 documented identifiers (base64 of UTF-8): the PII audit table in
# docs/data-compliance.md plus the "valid real-world codes" list that used to
# live in tests/test_uscc.py.
_ENCODED_NEEDLES = [
    "5ZCM5rWO5aSn5a2m",
    "6YOR5bee5Lqs5Lic5LyY5Yev6LS45piT5pyJ6ZmQ5YWs5Y+4",
    "5Y2X5Lqs6IuP5a6B5piT6LSt55S15a2Q5ZWG5Yqh5pyJ6ZmQ5YWs5Y+4",
    "MTIxMDAwMDA0MjUwMDYxMjVK",
    "OTE0NDAxODM3OTczNzA2NDlR",
    "546L5qKF",
    "5YiY5aiF",
    "5b6Q6L6w5bOw",
    "6LCi54ix6L+O",
    "5Lit5Zu95Yac5Lia6ZO26KGM5LiK5rW357+U5q635pSv6KGM",
    "Mjk4NTMzMTU3NzA4",
    "MDMzMjY3MA==",
    "OTEzMTAxMDdNQTFHMUM4UTVX",
    "OTEzMTAwMDBNQTFGUjhNQjFX",
    "OTExMTAzMDI1NjIxMzQ5MTZS",
]

PII_LITERALS = [base64.b64decode(n).decode("utf-8") for n in _ENCODED_NEEDLES]

#: Directories that are not part of the committed repository, or that hold
#: reference/vendored material we deliberately do not rewrite.
#: ``_source-archive`` is the read-only copy of the original coursework data
#: (git-ignored, per docs/data-compliance.md) and must not be modified.
SKIP_DIRS = {
    ".git",
    "_source-archive",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "docmind.egg-info",
    "dist",
    "build",
    "data",  # git-ignored runtime output (real uploads may land here)
}

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yml",
    ".yaml", ".toml", ".cfg", ".ini", ".html", ".css", ".example", ".env",
    ".sh", ".ps1", ".sql",
}


def iter_scannable_files(root):
    """Yield every text file under ``root`` that the guard should inspect."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if set(path.relative_to(root).parts) & SKIP_DIRS:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env"):
            yield path


def scan_for_pii(root, needles=None):
    """Return ``{relative_path: [matched literals]}`` for every hit."""
    needles = PII_LITERALS if needles is None else needles
    hits: dict[str, list[str]] = {}
    for path in iter_scannable_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = [n for n in needles if n in text]
        if found:
            hits[str(path.relative_to(root))] = found
    return hits


class TestScannerSelfTest:
    """A guard that cannot fail is worthless — verify it actually detects."""

    def test_detects_a_planted_literal(self, tmp_path):
        # Built from the constant so this file stays literal-free itself.
        (tmp_path / "leak.py").write_text(
            f'NAME = "{PII_LITERALS[0]}"\n', encoding="utf-8"
        )
        hits = scan_for_pii(tmp_path)
        assert list(hits) == ["leak.py"]
        assert hits["leak.py"] == [PII_LITERALS[0]]

    def test_clean_tree_reports_nothing(self, tmp_path):
        (tmp_path / "clean.py").write_text("NAME = 'synthetic'\n", encoding="utf-8")
        assert scan_for_pii(tmp_path) == {}

    def test_skips_ignored_directories(self, tmp_path):
        leak = tmp_path / "_source-archive" / "original"
        leak.mkdir(parents=True)
        (leak / "invoice.py").write_text(PII_LITERALS[0], encoding="utf-8")
        assert scan_for_pii(tmp_path) == {}

    def test_needle_list_is_not_silently_shrunk(self):
        # Guards against "fixing" a failure by deleting needles.
        assert len(PII_LITERALS) >= 15
        assert all(isinstance(n, str) and len(n) >= 2 for n in PII_LITERALS)


class TestRepositoryIsClean:
    def test_no_documented_pii_literal_in_the_repository(self):
        hits = scan_for_pii(find_repo_root())
        if hits:
            report = "\n".join(
                f"  {path}: {', '.join(literals)}" for path, literals in hits.items()
            )
            pytest.fail(
                "documented PII literals found in the repository "
                "(docs/data-compliance.md guarantees none are committed):\n"
                f"{report}\n"
                "Replace them with equivalent synthetic values — do not "
                "weaken this guard."
            )
