"""Компиляция проекта ``javac`` и разбор диагностик (docs/07 § 2.2).

    javac --release R -encoding UTF-8 -g -Xlint:all -d classes @sources.txt

``R`` — 21 (целевой уровень), а на более старом JDK — его собственная версия: так инструмент
работает и на машине с JDK 17, а в Docker и CI (JDK 21) собирает ровно под 21. Результат
кешируется в каталоге сборки по хешу содержимого, включая неудачные сборки.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from engine.runtime.jdk import detect_jdk, tool_path
from engine.runtime.workspace import Workspace

TARGET_RELEASE = 21
_RESULT = ".compile.json"
_HEADER = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): (?P<severity>error|warning|note): (?P<message>.*)$"
)
_GENERAL = re.compile(r"^(error|warning|Note): (.*)$")
_SUMMARY = re.compile(r"^\d+ (errors?|warnings?)$")
_JAVAC_OPTIONS = [
    "-J-Duser.language=en",
    "-J-Duser.country=US",
    "-J-Dfile.encoding=UTF-8",
    "-J-Dstderr.encoding=UTF-8",
    "-J-Dsun.stderr.encoding=UTF-8",
    "-encoding",
    "UTF-8",
    "-g",
    "-Xlint:all",
]


@dataclass(frozen=True, slots=True)
class CompileDiagnostic:
    file: str
    line: int
    column: int
    severity: str  # error | warning | note
    message: str
    end_line: int | None = None
    end_column: int | None = None


@dataclass(slots=True)
class CompileResult:
    ok: bool
    diagnostics: list[CompileDiagnostic]
    duration_ms: int
    cached: bool = False
    release: int = TARGET_RELEASE
    log: str = ""
    errors: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.errors = sum(1 for d in self.diagnostics if d.severity == "error")


def effective_release(jdk_major: int | None) -> int:
    return TARGET_RELEASE if jdk_major is None else min(TARGET_RELEASE, jdk_major)


def parse_diagnostics(output: str) -> list[CompileDiagnostic]:
    """Разбирает вывод javac: заголовок, строка исходника, строка с ``^`` (колонка), детали."""
    lines = output.replace("\r\n", "\n").split("\n")
    result: list[CompileDiagnostic] = []
    i = 0
    while i < len(lines):
        match = _HEADER.match(lines[i])
        if match is None:
            general = _GENERAL.match(lines[i])
            if general is not None:
                kind = general.group(1)
                severity = "note" if kind == "Note" else kind
                result.append(CompileDiagnostic("", 0, 0, severity, general.group(2)))
            i += 1
            continue
        message = [match.group("message")]
        column = 1
        i += 1
        if i + 1 < len(lines) and "^" in lines[i + 1] and not _HEADER.match(lines[i]):
            column = lines[i + 1].index("^") + 1
            i += 2
        while (
            i < len(lines)
            and lines[i]
            and not _HEADER.match(lines[i])
            and not _GENERAL.match(lines[i])
            and not _SUMMARY.match(lines[i])
        ):
            message.append(lines[i].strip())
            i += 1
        result.append(
            CompileDiagnostic(
                file=match.group("file").replace("\\", "/"),
                line=int(match.group("line")),
                column=column,
                severity=match.group("severity"),
                message="\n".join(message),
            )
        )
    return result


def _cacheable(ok: bool, diagnostics: list[CompileDiagnostic]) -> bool:
    """Кешируются успех и ошибки в коде. Сбой без привязки к файлу (таймаут, окружение) — нет."""
    return ok or any(d.file for d in diagnostics)


def _load_cached(workspace: Workspace) -> CompileResult | None:
    path = workspace.root / _RESULT
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not _cacheable(data["ok"], [CompileDiagnostic(**d) for d in data["diagnostics"]]):
        return None
    return CompileResult(
        ok=data["ok"],
        diagnostics=[CompileDiagnostic(**d) for d in data["diagnostics"]],
        duration_ms=data["duration_ms"],
        cached=True,
        release=data["release"],
        log=data["log"],
    )


@lru_cache(maxsize=4)
def _installed_major(java_home: Path | None) -> int | None:
    return detect_jdk(java_home).major


def compile_workspace(
    workspace: Workspace,
    java_home: Path | None = None,
    jdk_major: int | None = None,
    timeout_s: int = 30,
) -> CompileResult:
    """Собирает проект; повторный вызов с тем же хешем возвращает сохранённый результат."""
    cached = _load_cached(workspace)
    if cached is not None:
        return cached

    javac = tool_path("javac", java_home)
    if javac is None:
        raise FileNotFoundError("javac не найден: проверьте PATH или JB_JAVA_HOME")
    # версия JDK не передана — определяется по установленному: на JDK 17 цель 21 недоступна
    release = effective_release(jdk_major if jdk_major is not None else _installed_major(java_home))
    sources = workspace.sources
    if not sources:
        return CompileResult(
            False, [CompileDiagnostic("", 0, 0, "error", "нет .java файлов")], 0, release=release
        )

    arg_file = workspace.root / "sources.txt"
    arg_file.write_text("\n".join(f'"{s}"' for s in sources) + "\n", encoding="utf-8")
    command = [
        javac,
        "--release",
        str(release),
        *_JAVAC_OPTIONS,
        "-d",
        str(workspace.classes),
        f"@{arg_file}",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, cwd=workspace.src, capture_output=True, timeout=timeout_s, check=False
        )
        text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        ok = proc.returncode == 0
        diagnostics = parse_diagnostics(text)
    except subprocess.TimeoutExpired:
        text = f"javac не уложился в {timeout_s} с"
        ok = False
        diagnostics = [CompileDiagnostic("", 0, 0, "error", text)]
    duration_ms = int((time.monotonic() - started) * 1000)
    if ok is False and not any(d.severity == "error" for d in diagnostics):
        diagnostics.append(
            CompileDiagnostic("", 0, 0, "error", text.strip() or "javac завершился с ошибкой")
        )

    result = CompileResult(ok, diagnostics, duration_ms, False, release, text)
    if _cacheable(ok, diagnostics):
        (workspace.root / _RESULT).write_text(
            json.dumps(
                {
                    "ok": ok,
                    "diagnostics": [asdict(d) for d in diagnostics],
                    "duration_ms": duration_ms,
                    "release": release,
                    "log": text,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return result
