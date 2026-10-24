"""Настройки приложения. Все переменные окружения имеют префикс ``JB_`` (см. docs/11-deployment.md)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JB_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    db_url: str | None = None  # по умолчанию sqlite:///{data_dir}/app.db
    java_home: Path | None = None  # по умолчанию javac/java берутся из PATH
    samples_dir: Path = Path(__file__).resolve().parents[2] / "samples"  # встроенные примеры

    max_parallel_runs: int = Field(default_factory=lambda: min(4, os.cpu_count() or 2), ge=1)
    run_timeout_s: int = Field(default=10, ge=1)
    compile_timeout_s: int = Field(default=30, ge=1)
    output_limit_bytes: int = Field(default=1_048_576, ge=1024)
    jvm_xmx: str = "256m"
    keep_runs: bool = False

    log_level: str = "INFO"
    cors_origins: str = ""  # через запятую; нужно только при разработке без прокси Next.js
    seed_demo: bool = False

    @field_validator("data_dir")
    @classmethod
    def _absolute_data_dir(cls, value: Path) -> Path:
        # javac и java запускаются с другим cwd: относительные пути к сборкам перестали бы находиться
        return value.resolve()

    @property
    def database_url(self) -> str:
        return self.db_url or f"sqlite:///{(self.data_dir / 'app.db').resolve().as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        for sub in ("", "builds", "tmp"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
