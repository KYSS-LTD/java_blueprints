"""Реестр обработчиков видов запуска: ``kind`` → функция (S3.6, S6.3)."""

from __future__ import annotations

from app.jobs.generate_handler import generate_handlers
from app.jobs.handlers import default_handlers
from app.jobs.optimize_handler import optimize_handlers
from app.jobs.run_manager import Handler
from app.jobs.trace_handlers import trace_handlers
from app.jobs.verify_handlers import verify_handlers


def all_handlers() -> dict[str, Handler]:
    return {
        **default_handlers(),
        **verify_handlers(),
        **trace_handlers(),
        **optimize_handlers(),
        **generate_handlers(),
    }
