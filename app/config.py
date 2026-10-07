"""Settings for the local console.

Azure configuration deliberately does **not** live here. It belongs to
``evaluate_document_pii.build_config()``, which both the CLI and the web app
call — keeping one source for it is what stops the two surfaces drifting apart.
This module covers only how the server itself runs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Document PII Evaluation"
    host: str = "127.0.0.1"
    # 8000 and 8099 are commonly taken by the sibling underwriting-doc-classifier
    # console; pick a port that does not collide with it by default.
    port: int = 8100
    log_level: str = "INFO"
    max_upload_mb: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
