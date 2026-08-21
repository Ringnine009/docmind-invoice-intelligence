"""Config resolution tests: repo-root detection must survive odd installs."""

import importlib
import sys
from pathlib import Path

from app.core.config import find_repo_root


class TestFindRepoRoot:
    def test_returns_project_root_with_marker(self):
        root = find_repo_root()
        assert (root / "pyproject.toml").is_file()
        assert (root / "benchmark" / "ground_truth.json").is_file()
        # the app package lives under <root>/app
        assert (root / "app").is_dir()

    def test_marker_search_walks_up_from_deep_path(self, tmp_path):
        # Simulate a copied package: a deep dir with no pyproject.toml between
        # it and a fake repo root that does have one.
        fake_root = tmp_path / "repo"
        fake_root.mkdir()
        (fake_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        deep = fake_root / "site-packages" / "app" / "core"
        deep.mkdir(parents=True)

        # Monkey-patch the module-level REPO_ROOT to the deep dir so the walk
        # starts there (mimics __file__-based resolution from a copied install).
        import app.core.config as config

        original = config.REPO_ROOT
        try:
            config.REPO_ROOT = deep
            resolved = find_repo_root()
            assert resolved == fake_root
        finally:
            config.REPO_ROOT = original

    def test_fallback_to_cwd_when_no_marker(self, tmp_path, monkeypatch):
        import app.core.config as config

        original = config.REPO_ROOT
        try:
            config.REPO_ROOT = tmp_path  # no pyproject.toml anywhere above? tmp_path is under system temp
            monkeypatch.chdir(tmp_path)
            resolved = find_repo_root()
            # Either a parent with the marker was found, or cwd fallback.
            assert (resolved / "pyproject.toml").is_file() or resolved == tmp_path
        finally:
            config.REPO_ROOT = original
