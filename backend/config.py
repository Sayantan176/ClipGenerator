"""
backend/config.py
-----------------
Pydantic-settings configuration for ClipGenerator.
All values are loaded from environment variables / .env file at the project root.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from .env at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Groq API Configuration
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_whisper_model: str = "whisper-large-v3"

    # Background task execution
    # If False, jobs run in background worker threads (no Redis required).
    # If True, jobs are dispatched to Celery + Redis.
    use_celery: bool = False
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # Database
    database_url: str = "sqlite+aiosqlite:///./clipgenerator.db"
    db_echo: bool = False

    # Storage
    uploads_dir: Path = Path("uploads")
    outputs_dir: Path = Path("outputs")

    # Local Whisper fallback settings
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # FFmpeg custom path (if not in system PATH)
    ffmpeg_path: Optional[str] = None

    # Server settings
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    debug: bool = True

    # CORS
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    @field_validator("uploads_dir", "outputs_dir", mode="before")
    @classmethod
    def _make_path(cls, v: str | Path) -> Path:
        return Path(v)

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    return Settings()
