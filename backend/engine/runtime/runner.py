"""Запуск скомпилированной Java-программы с лимитами (docs/07 § 2.3–2.4).

Процесс стартует в пустом временном каталоге с чистым окружением, stdin подаётся целиком из
строки, вывод читается с ограничением по размеру, по таймауту и отмене убивается **всё дерево
процессов** (Windows — ``taskkill /T``, Linux — группа процессов). На Linux перед ``exec`` задаются
``setrlimit`` (CPU, размер файлов, число процессов).

Реализация синхронная и потоковая: она не зависит от политики цикла событий asyncio (на Windows
``uvicorn --reload`` использует селекторный цикл без подпроцессов); вызывающий код оборачивает её
в ``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.runtime.jdk import tool_path
from engine.runtime.workspace import Workspace

_TRACE_FILE = "hits.json"
_EXCEPTION = re.compile(
    r'Exception in thread "[^"]*" (?P<type>[\w.$]+)(?::[ \t]?(?P<rest>.*))?', re.S
)


@dataclass(frozen=True, slots=True)
class RunLimits:
    timeout_s: float = 10.0
    output_limit_bytes: int = 1_048_576
    xmx: str = "256m"
    xss: str = "1m"
    max_processes: int = 1024


@dataclass(slots=True)
class ExceptionInfo:
    type: str
    message: str | None
    frames: list[str]


@dataclass(slots=True)
class RunResult:
    status: str  # ok | exception | timeout | output_limit | cancelled | internal
    stdout: str
    stderr: str
    exit_code: int | None
    exception: ExceptionInfo | None
    duration_ms: int
    truncated: bool = False
    trace: dict[str, Any] | None = None  # содержимое ``hits.json`` у инструментированного запуска


def jvm_flags(limits: RunLimits, locale: str = "en_US", timezone: str = "UTC") -> list[str]:
    language, _, country = locale.partition("_")
    return [
        "-Dline.separator=\n",  # LF и на Windows: вывод детерминирован (эталоны сравниваются побайтно)
        "-Dfile.encoding=UTF-8",
        "-Dstdout.encoding=UTF-8",
        "-Dstderr.encoding=UTF-8",
        "-Dsun.stdout.encoding=UTF-8",
        "-Dsun.stderr.encoding=UTF-8",
        f"-Duser.language={language or 'en'}",
        f"-Duser.country={country or 'US'}",
        f"-Duser.timezone={timezone}",
        f"-Xmx{limits.xmx}",
        f"-Xss{limits.xss}",
        "-XX:+UseSerialGC",
        "-XX:TieredStopAtLevel=1",
        "-Xshare:auto",
    ]


def clean_env(java: str, home: Path) -> dict[str, str]:
    """Пустое окружение: только то, без чего JVM не стартует, и ``LANG=C.UTF-8``."""
    env = {
        "PATH": str(Path(java).parent),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(home),
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            if key in os.environ:
                env[key] = os.environ[key]
        env["TEMP"] = env["TMP"] = str(home)
    return env


def kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Убивает процесс и всех его потомков."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False
        )
    else:  # pragma: no cover - ветка Linux проверяется в CI
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def _posix_limits(limits: RunLimits) -> Callable[[], None]:  # pragma: no cover - Linux
    def apply() -> None:
        resource = importlib.import_module("resource")  # нет на Windows

        cpu = int(limits.timeout_s) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        fsize = max(limits.output_limit_bytes * 4, 1_048_576)
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))

    return apply


class _Reader(threading.Thread):
    """Читает поток процесса до лимита; при превышении сигналит и перестаёт читать."""

    def __init__(self, stream: object, limit: int, over: threading.Event) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.over = over
        self.data = bytearray()

    def run(self) -> None:
        read = self.stream.read1 if hasattr(self.stream, "read1") else self.stream.read  # type: ignore[attr-defined]
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    return
                room = self.limit - len(self.data)
                if len(chunk) > room:
                    self.data += chunk[: max(room, 0)]
                    self.over.set()
                    return
                self.data += chunk
        except (OSError, ValueError):
            return


def parse_exception(stderr: str) -> ExceptionInfo | None:
    """``Exception in thread "main" X: сообщение`` и кадры стека; стек в поведение не входит."""
    match = _EXCEPTION.search(stderr)
    if match is None:
        return None
    rest = match.group("rest") or ""
    lines = rest.split("\n")
    message_lines: list[str] = []
    for line in lines:
        if line.startswith(("\tat ", "Caused by:", "\t... ")):
            break
        message_lines.append(line)
    message = "\n".join(message_lines).strip() or None
    frames = [ln.strip() for ln in stderr[match.start() :].split("\n") if ln.startswith("\tat ")]
    return ExceptionInfo(match.group("type"), message, frames[:50])


def run_java(
    workspace: Workspace,
    main_class: str,
    *,
    stdin: str = "",
    args: Sequence[str] = (),
    java_home: Path | None = None,
    limits: RunLimits = RunLimits(),  # noqa: B008 - неизменяемый dataclass
    locale: str = "en_US",
    timezone: str = "UTC",
    cancel: threading.Event | None = None,
    trace_limit: int | None = None,
) -> RunResult:
    """Запускает ``main_class``; ``trace_limit`` включает сбор счётчиков ``JbTrace`` в ``RunResult.trace``."""
    java = tool_path("java", java_home)
    if java is None:
        return RunResult(
            "internal", "", "java не найден: проверьте PATH или JB_JAVA_HOME", None, None, 0
        )

    over = threading.Event()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="jb-run-") as run_dir:
        trace_file = Path(run_dir) / _TRACE_FILE
        trace_flags = (
            [f"-Djb.trace.out={trace_file}", f"-Djb.trace.limit={trace_limit}"]
            if trace_limit is not None
            else []
        )
        command = [
            java,
            *jvm_flags(limits, locale, timezone),
            *trace_flags,
            "-cp",
            str(workspace.classes),
            main_class,
            *args,
        ]
        env = clean_env(java, Path(run_dir))
        try:
            if sys.platform == "win32":
                process = subprocess.Popen(  # noqa: S603 - команда собрана из доверенных частей
                    command,
                    cwd=run_dir,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            else:  # pragma: no cover - Linux
                process = subprocess.Popen(  # noqa: S603
                    command,
                    cwd=run_dir,
                    env=env,
                    start_new_session=True,
                    preexec_fn=_posix_limits(limits),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
        except OSError as exc:
            return RunResult("internal", "", f"не удалось запустить java: {exc}", None, None, 0)

        out_reader = _Reader(process.stdout, limits.output_limit_bytes, over)
        err_reader = _Reader(process.stderr, limits.output_limit_bytes, over)
        out_reader.start()
        err_reader.start()

        def feed() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(stdin.encode("utf-8"))
                process.stdin.close()
            except (OSError, ValueError):
                pass

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()

        status = "ok"
        deadline = started + limits.timeout_s
        while True:
            try:
                process.wait(timeout=0.02)
                break
            except subprocess.TimeoutExpired:
                pass
            if over.is_set():
                status = "output_limit"
            elif cancel is not None and cancel.is_set():
                status = "cancelled"
            elif time.monotonic() > deadline:
                status = "timeout"
            else:
                continue
            kill_tree(process)
            process.wait()
            break

        out_reader.join(timeout=2)
        err_reader.join(timeout=2)
        writer.join(timeout=0.5)
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        trace = _read_trace(trace_file) if trace_limit is not None else None

    stdout = out_reader.data.decode("utf-8", errors="replace")
    stderr = err_reader.data.decode("utf-8", errors="replace")
    truncated = over.is_set()
    if status == "ok" and truncated:
        status = "output_limit"
    exception = parse_exception(stderr) if status in {"ok", "exception"} else None
    if status == "ok" and exception is not None and process.returncode not in (0, None):
        status = "exception"
    elif status == "ok":
        exception = None
    return RunResult(
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=process.returncode,
        exception=exception,
        duration_ms=int((time.monotonic() - started) * 1000),
        truncated=truncated,
        trace=trace,
    )


def _read_trace(path: Path) -> dict[str, Any] | None:
    """``hits.json`` пишет shutdown hook; при убийстве по таймауту файла нет."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "ExceptionInfo",
    "RunLimits",
    "RunResult",
    "clean_env",
    "jvm_flags",
    "kill_tree",
    "parse_exception",
    "run_java",
]
