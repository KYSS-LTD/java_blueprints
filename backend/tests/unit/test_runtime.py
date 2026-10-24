"""Сборка и запуск Java (S3.4, S3.5): чистые части без JDK и сквозные тесты с JDK (метка `jdk`)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.config import Settings
from engine.runtime.compiler import (
    CompileDiagnostic,
    compile_workspace,
    effective_release,
    parse_diagnostics,
)
from engine.runtime.jdk import detect_jdk
from engine.runtime.runner import (
    RunLimits,
    clean_env,
    jvm_flags,
    parse_exception,
    run_java,
)
from engine.runtime.workspace import materialize

# --- чистые части ---------------------------------------------------------------------------------


def test_effective_release_is_capped_by_the_installed_jdk():
    assert effective_release(None) == 21
    assert effective_release(25) == 21
    assert effective_release(21) == 21
    assert effective_release(17) == 17


def test_parse_diagnostics_extracts_position_message_and_details():
    output = (
        "Main.java:3: error: cannot find symbol\n"
        "        int x = переменная;\n"
        "                ^\n"
        "  symbol:   variable переменная\n"
        "  location: class Main\n"
        "pkg/Util.java:10: warning: [serial] serializable class has no serialVersionUID\n"
        "class Util extends Exception {\n"
        "^\n"
        "Note: Main.java uses unchecked or unsafe operations.\n"
        "1 error\n"
        "1 warning\n"
    )
    first, second, note = parse_diagnostics(output)
    assert (first.file, first.line, first.column, first.severity) == ("Main.java", 3, 17, "error")
    assert first.message.splitlines() == [
        "cannot find symbol",
        "symbol:   variable переменная",
        "location: class Main",
    ]
    assert (second.file, second.line, second.column, second.severity) == (
        "pkg/Util.java",
        10,
        1,
        "warning",
    )
    assert second.message.startswith("[serial]")
    assert (note.file, note.line, note.severity) == ("", 0, "note")


def test_parse_diagnostics_handles_windows_paths_and_empty_output():
    out = "C:\\x\\Main.java:1: error: ';' expected\nint a\n     ^\n"
    # путь с диском разбирается нежадно: берётся всё до «:номер: error»
    (diag,) = parse_diagnostics(out)
    assert diag.line == 1 and diag.column == 6 and diag.file.endswith("Main.java")
    assert parse_diagnostics("") == []


def test_parse_exception_extracts_type_message_and_frames():
    stderr = (
        'Exception in thread "main" java.lang.IllegalStateException: что-то пошло не так\n'
        "\tat Main.f(Main.java:5)\n"
        "\tat Main.main(Main.java:9)\n"
    )
    info = parse_exception(stderr)
    assert info is not None
    assert info.type == "java.lang.IllegalStateException"
    assert info.message == "что-то пошло не так"
    assert info.frames == ["at Main.f(Main.java:5)", "at Main.main(Main.java:9)"]
    no_message = parse_exception(
        'Exception in thread "main" java.lang.NullPointerException\n\tat Main.main(Main.java:3)\n'
    )
    assert no_message is not None and no_message.message is None
    assert parse_exception("просто текст в stderr") is None


def test_jvm_flags_pin_encoding_locale_and_memory():
    flags = jvm_flags(RunLimits(xmx="128m"), locale="ru_RU", timezone="Europe/Moscow")
    assert "-Dfile.encoding=UTF-8" in flags and "-Dstdout.encoding=UTF-8" in flags
    assert "-Duser.language=ru" in flags and "-Duser.country=RU" in flags
    assert "-Duser.timezone=Europe/Moscow" in flags
    assert "-Xmx128m" in flags


def test_clean_env_is_minimal(tmp_path):
    env = clean_env(str(tmp_path / "jdk" / "bin" / "java"), tmp_path)
    assert env["LANG"] == "C.UTF-8" and env["HOME"] == str(tmp_path)
    assert env["PATH"] == str(tmp_path / "jdk" / "bin")
    assert "JAVA_TOOL_OPTIONS" not in env and "USERPROFILE" not in env


def test_materialize_writes_sources_once_and_keeps_paths(tmp_path):
    files = {"Main.java": "class Main {}", "pkg/Util.java": "package pkg; class Util {}"}
    workspace = materialize(files, tmp_path, "abc123")
    assert workspace.sources == ["Main.java", "pkg/Util.java"]
    assert (
        (workspace.src / "pkg" / "Util.java").read_text(encoding="utf-8").startswith("package pkg")
    )
    marker = workspace.src / "Main.java"
    marker.write_text("изменено вручную", encoding="utf-8")
    again = materialize(files, tmp_path, "abc123")  # тот же хеш: каталог не пересоздаётся
    assert again.root == workspace.root
    assert marker.read_text(encoding="utf-8") == "изменено вручную"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_materialize_rejects_paths_that_escape_the_build_directory(tmp_path):
    with pytest.raises(ValueError, match="выходит"):
        materialize({"../evil.java": "x"}, tmp_path, "deadbeef")
    assert not (tmp_path / "evil.java").exists()


def test_diagnostic_is_a_plain_value():
    assert CompileDiagnostic("A.java", 1, 2, "error", "m") == CompileDiagnostic(
        "A.java", 1, 2, "error", "m"
    )


# --- с JDK -----------------------------------------------------------------------------------------

HELLO = """
import java.util.Scanner;
public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in, "UTF-8");
        String name = in.hasNextLine() ? in.nextLine() : "мир";
        System.out.println("Привет, " + name + "!");
        System.err.println("служебное");
        System.out.println(args.length);
    }
}
"""


@pytest.fixture(scope="module")
def java_home() -> Path | None:
    return Settings().java_home


@pytest.fixture(scope="module")
def jdk_major(java_home) -> int:
    info = detect_jdk(java_home)
    if info.major is None:
        pytest.fail("JDK не найден: задайте JB_JAVA_HOME или положите javac в PATH")
    return info.major


def build(files, tmp_path, java_home, jdk_major, name="b"):
    workspace = materialize(files, tmp_path / "builds", name)
    return workspace, compile_workspace(workspace, java_home, jdk_major)


@pytest.mark.jdk
def test_compile_success_and_cache_hit(tmp_path, java_home, jdk_major):
    workspace, first = build({"Main.java": HELLO}, tmp_path, java_home, jdk_major)
    assert first.ok and first.errors == 0 and not first.cached
    assert (workspace.classes / "Main.class").exists()
    assert first.release == effective_release(jdk_major)
    second = compile_workspace(workspace, java_home, jdk_major)
    assert second.ok and second.cached and second.duration_ms == first.duration_ms


@pytest.mark.jdk
def test_compile_error_has_position_and_cyrillic_message(tmp_path, java_home, jdk_major):
    source = "public class Main {\n    void f() {\n        int x = переменная;\n    }\n}\n"
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert not result.ok and result.errors == 1
    (error,) = [d for d in result.diagnostics if d.severity == "error"]
    assert (error.file, error.line) == ("Main.java", 3)
    assert error.column == 17
    assert "cannot find symbol" in error.message and "переменная" in error.message
    cached = compile_workspace(workspace, java_home, jdk_major)
    assert cached.cached and not cached.ok and cached.diagnostics == result.diagnostics


@pytest.mark.jdk
def test_compile_keeps_package_paths_and_reports_warnings(tmp_path, java_home, jdk_major):
    files = {
        "Main.java": "public class Main { public static void main(String[] a) { new pkg.Util(); } }",
        "pkg/Util.java": "package pkg;\npublic class Util extends Exception { }\n",
    }
    workspace, result = build(files, tmp_path, java_home, jdk_major)
    assert result.ok
    assert (workspace.classes / "pkg" / "Util.class").exists()
    warnings = [d for d in result.diagnostics if d.severity == "warning"]
    assert any(w.file == "pkg/Util.java" and "serial" in w.message for w in warnings)


@pytest.mark.jdk
def test_run_with_cyrillic_stdin_stdout_and_args(tmp_path, java_home, jdk_major):
    workspace, result = build({"Main.java": HELLO}, tmp_path, java_home, jdk_major)
    assert result.ok
    ran = run_java(workspace, "Main", stdin="Анастасия\n", args=["а", "б"], java_home=java_home)
    assert ran.status == "ok" and ran.exit_code == 0 and not ran.truncated
    assert ran.stdout.replace("\r\n", "\n") == "Привет, Анастасия!\n2\n"
    assert ran.stderr.strip() == "служебное"
    assert ran.exception is None and ran.duration_ms >= 0


@pytest.mark.jdk
def test_run_empty_stdin_and_exit_code(tmp_path, java_home, jdk_major):
    source = 'public class Main { public static void main(String[] a) { System.out.println("ok"); System.exit(3); } }'
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert result.ok
    ran = run_java(workspace, "Main", java_home=java_home)
    assert ran.status == "ok" and ran.exit_code == 3 and ran.stdout.strip() == "ok"


@pytest.mark.jdk
def test_run_reports_uncaught_exception(tmp_path, java_home, jdk_major):
    source = (
        "public class Main { public static void main(String[] a) {\n"
        '    System.out.println("до");\n'
        '    throw new IllegalStateException("сломалось: п = 5");\n'
        "} }"
    )
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert result.ok
    ran = run_java(workspace, "Main", java_home=java_home)
    assert ran.status == "exception" and ran.exit_code == 1
    assert ran.stdout.strip() == "до"
    assert ran.exception is not None
    assert ran.exception.type == "java.lang.IllegalStateException"
    assert ran.exception.message == "сломалось: п = 5"
    assert ran.exception.frames and ran.exception.frames[0].startswith("at Main.main")


@pytest.mark.jdk
def test_run_timeout_kills_an_infinite_loop(tmp_path, java_home, jdk_major):
    source = 'public class Main { public static void main(String[] a) { System.out.println("старт"); System.out.flush(); while (true) { } } }'
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert result.ok
    started = time.monotonic()
    ran = run_java(workspace, "Main", java_home=java_home, limits=RunLimits(timeout_s=1.0))
    assert ran.status == "timeout"
    assert time.monotonic() - started < 8
    assert ran.stdout.strip() == "старт"


@pytest.mark.jdk
def test_run_output_limit_truncates_and_stops_the_process(tmp_path, java_home, jdk_major):
    source = 'public class Main { public static void main(String[] a) { StringBuilder s = new StringBuilder("x".repeat(1000)); while (true) { System.out.println(s); } } }'
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert result.ok
    ran = run_java(
        workspace,
        "Main",
        java_home=java_home,
        limits=RunLimits(timeout_s=20, output_limit_bytes=10_000),
    )
    assert ran.status == "output_limit" and ran.truncated
    assert 0 < len(ran.stdout.encode("utf-8")) <= 10_000


@pytest.mark.jdk
def test_cancel_event_stops_a_running_program(tmp_path, java_home, jdk_major):
    source = "public class Main { public static void main(String[] a) { while (true) { } } }"
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert result.ok
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    ran = run_java(
        workspace, "Main", java_home=java_home, limits=RunLimits(timeout_s=30), cancel=cancel
    )
    assert ran.status == "cancelled"
    assert time.monotonic() - started < 10


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        )
        return str(pid) in out.stdout
    import os

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.mark.jdk
def test_timeout_kills_the_whole_process_tree(tmp_path, java_home, jdk_major):
    source = """
public class Main {
    public static void main(String[] a) throws Exception {
        String[] cmd = System.getProperty("os.name").startsWith("Windows")
            ? new String[] {"ping", "-n", "60", "127.0.0.1"}
            : new String[] {"/bin/sleep", "60"};  // PATH в чистом окружении запуска — только каталог Java
        Process child = new ProcessBuilder(cmd).start();
        System.out.println(child.pid());
        System.out.flush();
        Thread.sleep(60000);
    }
}
"""
    workspace, result = build({"Main.java": source}, tmp_path, java_home, jdk_major)
    assert result.ok
    ran = run_java(workspace, "Main", java_home=java_home, limits=RunLimits(timeout_s=3))
    assert ran.status == "timeout"
    pid = int(ran.stdout.strip().splitlines()[0])
    time.sleep(0.5)
    assert not _alive(pid), "потомок процесса пережил таймаут"


def test_environment_failure_is_not_cached(tmp_path: Path) -> None:
    # сбой без привязки к файлу (например, javac не нашёл @-файл) не должен навсегда залипнуть в кеше
    from engine.runtime.compiler import _cacheable

    located = CompileDiagnostic("Main.java", 3, 5, "error", "oops")
    environment = CompileDiagnostic("", 0, 0, "error", "file not found")
    assert _cacheable(True, [])
    assert _cacheable(False, [located])
    assert not _cacheable(False, [environment])
