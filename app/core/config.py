"""Application configuration loaded from environment variables.

Secrets are never hard-coded. Values come from (in priority order):
  1. the file pointed to by ``DOCMIND_ENV_FILE`` (if set),
  2. ``<repo-root>/.env``,
  3. the parent directory's ``.env`` (e.g. a shared workspace secrets file),
  4. the process environment.

DashScope keys use their native ``DASHSCOPE_*`` names; DocMind-specific
settings use the ``DOCMIND_`` prefix.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def find_repo_root() -> Path:
    """Locate the repository root robustly.

    Primary: walk up from this file until a directory containing
    ``pyproject.toml`` is found (works for both source checkouts and
    editable installs). Fallback: the process working directory. This keeps
    data-file lookups (e.g. ``benchmark/ground_truth.json``) working even if
    the ``app`` package is ever imported from a copied (non-editable) install.
    """
    marker = "pyproject.toml"
    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        if (parent / marker).is_file():
            return parent
    return Path.cwd()


def _env_file_candidates() -> tuple[Path, ...]:
    explicit = os.environ.get("DOCMIND_ENV_FILE")
    candidates: list[Path] = [Path(explicit)] if explicit else []
    candidates += [REPO_ROOT / ".env", REPO_ROOT.parent / ".env"]
    return tuple(c for c in candidates if c.is_file())


class Settings(BaseSettings):
    """Runtime settings for DocMind (all fields optional, env-driven)."""

    model_config = SettingsConfigDict(
        env_file=_env_file_candidates(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- DashScope (Alibaba Cloud Bailian) OpenAI-compatible endpoint ---
    dashscope_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "dashscope_api_key"),
    )
    dashscope_openai_compat_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices(
            "DASHSCOPE_OPENAI_COMPAT_URL", "dashscope_openai_compat_url"
        ),
    )
    vision_model_primary: str = Field(
        default="qwen-vl-plus",
        validation_alias=AliasChoices(
            "DOCMIND_VISION_MODEL_PRIMARY", "vision_model_primary"
        ),
    )
    vision_model_fallback: str = Field(
        default="qwen3.5-ocr",
        validation_alias=AliasChoices(
            "DOCMIND_VISION_MODEL_FALLBACK", "vision_model_fallback"
        ),
    )

    # --- Extraction ---
    max_workers: int = Field(
        default=4,
        validation_alias=AliasChoices("DOCMIND_MAX_WORKERS", "max_workers"),
    )
    pdf_render_dpi: int = Field(
        default=200,
        validation_alias=AliasChoices("DOCMIND_PDF_RENDER_DPI", "pdf_render_dpi"),
    )

    # --- Audit engine ---
    arith_tolerance: float = Field(
        default=0.02,
        validation_alias=AliasChoices("DOCMIND_ARITH_TOLERANCE", "arith_tolerance"),
    )
    allowed_tax_rates: list[float] = Field(
        default_factory=lambda: [0, 1, 3, 5, 6, 9, 13],
        validation_alias=AliasChoices(
            "DOCMIND_ALLOWED_TAX_RATES", "allowed_tax_rates"
        ),
    )
    low_confidence_threshold: float = Field(
        default=0.6,
        validation_alias=AliasChoices(
            "DOCMIND_LOW_CONFIDENCE_THRESHOLD", "low_confidence_threshold"
        ),
    )

    # --- Storage / server ---
    data_dir: str = Field(
        default="data",
        validation_alias=AliasChoices("DOCMIND_DATA_DIR", "data_dir"),
    )
    host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("DOCMIND_HOST", "host"),
    )
    port: int = Field(
        default=8000,
        validation_alias=AliasChoices("DOCMIND_PORT", "port"),
    )

    @field_validator("allowed_tax_rates", mode="before")
    @classmethod
    def _parse_tax_rates(cls, v):
        if isinstance(v, str):
            return [float(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else (REPO_ROOT / p)

    def require_api_key(self) -> str:
        """Raise a helpful error if no DashScope key is configured."""
        if not self.dashscope_api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is not configured. Copy .env.example to .env "
                "(or point DOCMIND_ENV_FILE at a file containing the key) and retry."
            )
        return self.dashscope_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
