#!/usr/bin/env python3
"""Pre-commit secret scanner.

Scans the repository (excluding .git, _source, .venv, node_modules, data)
for common secret patterns: API keys, private keys, and the exact credential
strings that were hard-coded in the original coursework.

Usage:
    python scripts/scan_secrets.py
Exit code 0 = clean, 1 = matches found.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Generic secret patterns.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("openai/azure key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bghp_[0-9A-Za-z]{30,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("dashscope key", re.compile(r"\bsk-ws-[A-Za-z0-9_\-\.]{20,}\b")),
]

_SKIP_DIRS = {".git", "_source", ".venv", "venv", "node_modules", "data", "dist", "__pycache__"}
_SKIP_FILES = {".env", ".env.example", "scan_secrets.py"}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".pyc", ".xlsx", ".pptx", ".mp4", ".docx"}

# Additional exact strings to forbid can be supplied via the environment
# (comma separated), e.g. the leaked coursework key:
#   DOCMIND_FORBIDDEN_KEYS=sk-xxx,AIza...   python scripts/scan_secrets.py
# The generic patterns above already cover the leaked key's format
# (AIza[0-9A-Za-z_-]{20,}), so the repository never needs to contain the
# secret itself.


def _extra_forbidden() -> list[str]:
    raw = os.environ.get("DOCMIND_FORBIDDEN_KEYS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def main() -> int:
    hits: list[tuple[str, str, str]] = []

    def scan_file(path: Path) -> None:
        if path.suffix in _SKIP_SUFFIXES:
            return
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        for line_no, line in enumerate(text.splitlines(), 1):
            for forbidden in _extra_forbidden():
                if forbidden in line:
                    hits.append((str(path), str(line_no), "forbidden key (env DOCMIND_FORBIDDEN_KEYS)"))
            for name, pattern in _PATTERNS:
                if pattern.search(line):
                    hits.append((str(path), str(line_no), name))

    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        # any `_source*` directory holds the original coursework (leaked key
        # + real PII) — never scan or commit it
        if any(part.startswith("_source") for part in rel.parts):
            continue
        if path.name in _SKIP_FILES:
            continue
        scan_file(path)

    if hits:
        print(f"SECRET SCAN FAILED — {len(hits)} match(es):")
        for path, line, kind in hits[:30]:
            print(f"  {path}:{line}  ({kind})")
        return 1
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
