"""Настройки: путь к данным всегда абсолютный."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_relative_data_dir_becomes_absolute() -> None:
    # javac и java запускаются с другим cwd, поэтому относительный путь к сборкам ломал @sources.txt и -cp
    settings = Settings(data_dir=Path("relative-data"))
    assert settings.data_dir.is_absolute()
    assert settings.data_dir == Path("relative-data").resolve()
