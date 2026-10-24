"""Сборка версии: материализация файлов, javac, кеш по хешу, диагностики с привязкой к узлам (S3.4)."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from app.config import Settings
from app.errors import ApiError
from app.schemas.common import Diagnostic
from app.schemas.verify import BuildResult
from engine.java.project import JavaProject, get_project
from engine.runtime.compiler import CompileResult, compile_workspace
from engine.runtime.jdk import JdkInfo, detect_jdk
from engine.runtime.trace import TraceBuild
from engine.runtime.trace import prepare_trace as prepare_trace_build
from engine.runtime.workspace import Workspace, materialize


@lru_cache(maxsize=4)
def _jdk(java_home: Path | None) -> JdkInfo:
    return detect_jdk(java_home)


def jdk_info(settings: Settings) -> JdkInfo:
    """JDK определяется один раз на процесс: ``javac -version`` стоит сотни миллисекунд."""
    return _jdk(settings.java_home)


def unavailable(message: str) -> ApiError:
    return ApiError(503, "JDK_UNAVAILABLE", message)


def prepare(
    settings: Settings, files: Mapping[str, str], content_hash: str
) -> tuple[Workspace, CompileResult]:
    """Собирает файлы в ``builds/<hash>``; повторная сборка того же содержимого берётся из кеша."""
    jdk = jdk_info(settings)
    if jdk.javac is None:
        raise unavailable("javac не найден: проверьте PATH или JB_JAVA_HOME")
    workspace = materialize(files, settings.data_dir / "builds", content_hash)
    try:
        result = compile_workspace(
            workspace, settings.java_home, jdk.major, settings.compile_timeout_s
        )
    except FileNotFoundError as exc:
        raise unavailable(str(exc)) from exc
    return workspace, result


def prepare_trace(settings: Settings, files: Mapping[str, str]) -> TraceBuild:
    """Инструментированная сборка версии (``TraceUnavailable`` — копия не компилируется)."""
    jdk = jdk_info(settings)
    if jdk.javac is None:
        raise unavailable("javac не найден: проверьте PATH или JB_JAVA_HOME")
    try:
        return prepare_trace_build(
            get_project(files),
            settings.data_dir / "builds",
            java_home=settings.java_home,
            jdk_major=jdk.major,
            timeout_s=settings.compile_timeout_s,
        )
    except FileNotFoundError as exc:
        raise unavailable(str(exc)) from exc


def diagnostics(result: CompileResult, project: JavaProject) -> list[Diagnostic]:
    """Диагностики javac в формате API; ``node_id`` — самый глубокий узел графа под позицией."""
    found: list[Diagnostic] = []
    for item in result.diagnostics:
        node_id = None
        if item.file and item.line > 0:
            try:
                location = project.locate(item.file, item.line, max(item.column, 1))
            except Exception:  # noqa: BLE001 - привязка к узлу не должна ломать ответ сборки
                location = None
            node_id = location.node_id if location is not None else None
        found.append(
            Diagnostic(
                file=item.file,
                line=item.line,
                column=item.column,
                end_line=item.end_line,
                end_column=item.end_column,
                severity=item.severity,  # type: ignore[arg-type]
                message=item.message,
                node_id=node_id,
            )
        )
    return found


def build(settings: Settings, files: Mapping[str, str], content_hash: str) -> BuildResult:
    _, result = prepare(settings, files, content_hash)
    return BuildResult(
        ok=result.ok,
        diagnostics=diagnostics(result, get_project(files)),
        duration_ms=result.duration_ms,
        cached=result.cached,
    )
