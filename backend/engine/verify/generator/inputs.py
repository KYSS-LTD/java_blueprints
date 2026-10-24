"""Входы программы для генератора сценариев: seed-набор и мутации (S6.9, прототип).

Вход — текст stdin и аргументы командной строки. Всё детерминировано: случайность берётся из
переданного ``random.Random`` (seed выводится из хеша проекта), порядок словарей и множеств
фиксирован.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from engine.verify.generator.input_schema import InputHints, SchemaNode

MAX_INPUT_BYTES = 200_000
MAX_LINES = 400

INT_POOL = [0, 1, 2, 3, 4, 5, 7, 10, 42, 100, 1000, -1, -5, 2147483647, 2147483648]
DOUBLE_POOL = ["0.0", "0.5", "1.5", "2.5", "10.0", "45.5", "99.99", "-1.0", "1e9", "3.14159"]
WORD_POOL = ["abc", "test", "x", "Иванов", "Пётр", "item", "A", "ключ"]
BAD_POOL = ["", "abc", "1.5", "NaN", "-", "999999999999999999999", "#", "0x10", "--", "ё"]
LONG_TOKEN = "q" * 120


@dataclass(frozen=True, slots=True)
class TestInput:
    __test__ = False  # не тест pytest

    stdin: str
    args: tuple[str, ...] = ()
    origin: str = "seed"  # empty | single | multi | boundary | invalid | existing | mutant | args

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        return self.stdin, self.args


def split_lines(stdin: str) -> list[str]:
    if not stdin:
        return []
    lines = stdin.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def join_lines(lines: Sequence[str]) -> str:
    text = "\n".join(lines)
    return text + "\n" if lines else ""


def _tokens(line: str, delimiter: str) -> list[str]:
    return line.split() if delimiter == " " else line.split(delimiter)


def _join(tokens: Sequence[str], delimiter: str) -> str:
    return delimiter.join(tokens)


def _is_int(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


class Pools:
    """Значения токенов по типам: числа из условий программы, её слова и общие граничные."""

    def __init__(self, hints: InputHints) -> None:
        constants: list[str] = []
        for value in hints.numbers:
            if value == int(value) and abs(value) < 2**40:
                constants.extend(str(int(value) + d) for d in (-1, 0, 1))
            else:
                constants.append(repr(value))
        self.ints = list(dict.fromkeys([*constants, *map(str, INT_POOL)]))
        self.numeric_constants = [c for c in constants if _is_number(c)]
        self.doubles = list(dict.fromkeys([*DOUBLE_POOL]))
        words = [w for w in hints.words if not _is_number(w) and " " not in w and w != "#"]
        self.words = list(dict.fromkeys([*words, *WORD_POOL]))
        self.commands = list(hints.commands) or [w for w in hints.words if w.isalpha()]

    def token(self, rng: random.Random) -> str:
        roll = rng.random()
        if roll < 0.25 and self.numeric_constants:
            return rng.choice(self.numeric_constants)
        if roll < 0.55:
            return rng.choice(self.words)
        if roll < 0.78:
            return rng.choice(self.ints)
        if roll < 0.92:
            return rng.choice(self.doubles)
        if roll < 0.97:
            return rng.choice(BAD_POOL)
        return LONG_TOKEN


# --- seed-набор ---------------------------------------------------------------------------------------


def _typed(kind: str | None, pools: Pools, rng: random.Random) -> str:
    if kind in {"int", "long"}:
        return rng.choice(pools.ints[:12])
    if kind == "double":
        return rng.choice(pools.doubles[:6])
    if kind == "word":
        return rng.choice(pools.words)
    return "text " + rng.choice(pools.words)


def _from_schema(
    nodes: list[SchemaNode],
    pools: Pools,
    rng: random.Random,
    size: int,
    counts: dict[str, int],
    delimiter: str,
) -> list[str]:
    """Строки ввода по схеме; ``size`` — сколько раз повторять циклы чтения."""
    lines: list[str] = []
    pending: list[str] = []  # токены одной строки, прочитанные ``next*`` подряд

    def flush() -> None:
        if pending:
            lines.append(_join(pending, delimiter))
            pending.clear()

    for node in nodes:
        if node.kind == "token":
            if node.var and node.var in counts:
                pending.append(str(size))
            else:
                pending.append(_typed(node.type, pools, rng))
        elif node.kind == "line":
            flush()
            lines.append(
                str(size) if node.var and node.var in counts else _typed(node.type, pools, rng)
            )
        elif node.kind == "repeat":
            flush()
            times = (
                size
                if node.count_ref in {"EOF", None} or node.count_ref in counts
                else _count(node)
            )
            for _ in range(times):
                lines.extend(_from_schema(node.children, pools, rng, size, counts, delimiter))
        elif node.kind == "command_loop":
            flush()
            for _ in range(max(size, 1)):
                lines.extend(_commands(node, pools, rng, delimiter))
    flush()
    return lines


def _count(node: SchemaNode) -> int:
    try:
        return min(int(float(node.count_ref or "1")), 5)
    except ValueError:
        return 1


def _commands(node: SchemaNode, pools: Pools, rng: random.Random, delimiter: str) -> list[str]:
    lines = []
    for label in node.labels[:40]:
        arity = rng.randint(0, 4)
        lines.append(_join([label, *(pools.token(rng) for _ in range(arity))], delimiter))
    return lines


def _count_refs(nodes: list[SchemaNode]) -> dict[str, int]:
    found: dict[str, int] = {}
    for node in nodes:
        if node.kind == "repeat" and node.count_ref and node.count_ref not in {"EOF"}:
            found[node.count_ref] = 1
        found.update(_count_refs(node.children))
    return found


_TERMINATORS = frozenset({"quit", "exit", "stop", "end", "bye", "q", "halt", "finish", "done", "x"})
_CRASHERS = frozenset({"boom", "crash", "fail", "error", "throw", "panic", "abort"})


def _command_seeds(hints: InputHints) -> list[TestInput]:
    """Ввод для программ с командами: каждая команда с 0…6 «универсальными» аргументами.

    Токен ``1`` годится и как целое, и как дробное, и как строка, поэтому строка ``add 1 1 1 1 1``
    обычно проходит разбор любого формата. Команды выхода и аварийные ставятся отдельными
    вводами: после них остальные строки не выполняются.
    """
    delimiter = hints.delimiter
    stopping = _TERMINATORS | _CRASHERS
    ordinary = [c for c in hints.commands[:60] if c.lower() not in stopping]
    special = [c for c in hints.commands[:60] if c.lower() in stopping]
    seeds: list[TestInput] = []
    for token in ("1", "1.5", "Иванов"):
        for arity in (0, 1, 2, 3, 4, 5, 6):
            if token != "1" and arity not in (1, 2, 5):
                continue
            lines = [_join([c, *([token] * arity)], delimiter) for c in ordinary]
            seeds.append(TestInput(join_lines(lines), (), "multi"))
    mixed = [_join([c, "1", "Иванов", "Хлеб", "2", "45.5"], delimiter) for c in ordinary]
    seeds.append(TestInput(join_lines(mixed), (), "multi"))
    seeds.extend(TestInput(join_lines([c]), (), "boundary") for c in special)
    return seeds


def seed_inputs(
    hints: InputHints, examples: Sequence[TestInput], rng: random.Random
) -> list[TestInput]:
    """Начальный набор: пустой ввод, по схеме малый и средний, граничные значения, неверный формат."""
    pools = Pools(hints)
    seeds: list[TestInput] = [TestInput("", (), "empty")]
    if not hints.reads_stdin:
        if hints.uses_args:
            for args in (("1",), ("abc",), ("1", "2"), ("-1",), ("",), ("0", "x", "y")):
                seeds.append(TestInput("", args, "args"))
        return seeds
    counts = _count_refs(hints.schema)
    if hints.inferred:
        for origin, size in (("single", 1), ("multi", 3), ("multi", 6)):
            lines = _from_schema(hints.schema, pools, rng, size, counts, hints.delimiter)
            seeds.append(TestInput(join_lines(lines), (), origin))
        zero = _from_schema(hints.schema, pools, rng, 0, counts, hints.delimiter)
        seeds.append(TestInput(join_lines(zero), (), "boundary"))
        for _ in range(3):  # командные строки с разным числом аргументов
            lines = _from_schema(hints.schema, pools, rng, 2, counts, hints.delimiter)
            seeds.append(TestInput(join_lines(lines), (), "multi"))
    if hints.commands:
        seeds.extend(_command_seeds(hints))
    seeds.extend(
        [
            TestInput("0\n", (), "boundary"),
            TestInput("1\n", (), "single"),
            TestInput("-1\n", (), "boundary"),
            TestInput("abc\n", (), "invalid"),
            TestInput("1 2 3\n", (), "multi"),
            TestInput("text line\nsecond line\n", (), "multi"),
            TestInput("\n\n", (), "boundary"),
            TestInput("2147483647\n", (), "boundary"),
            TestInput("5\n" + "\n".join(str(i) for i in range(1, 6)) + "\n", (), "multi"),
        ]
    )
    if hints.uses_args:
        seeds.append(TestInput("", ("1",), "args"))
    seeds.extend(examples)
    return list(dict.fromkeys(seeds))


# --- мутации --------------------------------------------------------------------------------------------


class Mutator:
    def __init__(self, hints: InputHints, templates: Sequence[str], rng: random.Random) -> None:
        self.hints = hints
        self.pools = Pools(hints)
        self.rng = rng
        self.templates = [t for t in templates if t.strip()]
        self.delimiter = hints.delimiter

    # --- операторы над строками -------------------------------------------------------------------

    def _pick(self, lines: list[str]) -> int | None:
        return self.rng.randrange(len(lines)) if lines else None

    def replace_token(self, lines: list[str]) -> list[str]:
        index = self._pick(lines)
        if index is None:
            return [self.pools.token(self.rng)]
        tokens = _tokens(lines[index], self.delimiter) or [""]
        slot = self.rng.randrange(len(tokens))
        tokens[slot] = self.pools.token(self.rng)
        return [*lines[:index], _join(tokens, self.delimiter), *lines[index + 1 :]]

    def tweak_number(self, lines: list[str]) -> list[str]:
        spots = [
            (i, j)
            for i, line in enumerate(lines)
            for j, tok in enumerate(_tokens(line, self.delimiter))
            if _is_number(tok)
        ]
        if not spots:
            return self.replace_token(lines)
        i, j = self.rng.choice(spots)
        tokens = _tokens(lines[i], self.delimiter)
        value = float(tokens[j])
        step = self.rng.choice([-1, 1, 0, 2, -10, 10, 1000])
        new = str(int(value) + step) if _is_int(tokens[j]) else repr(round(value + step * 0.5, 3))
        if self.rng.random() < 0.15:
            new = self.rng.choice(["0", "-1", "2147483647", "1e308"])
        tokens[j] = new
        return [*lines[:i], _join(tokens, self.delimiter), *lines[i + 1 :]]

    def delete_line(self, lines: list[str]) -> list[str]:
        index = self._pick(lines)
        return lines if index is None else [*lines[:index], *lines[index + 1 :]]

    def duplicate_line(self, lines: list[str]) -> list[str]:
        index = self._pick(lines)
        if index is None:
            return lines
        times = self.rng.choice([1, 1, 2, 5, 12])
        return [*lines[: index + 1], *([lines[index]] * times), *lines[index + 1 :]]

    def swap_lines(self, lines: list[str]) -> list[str]:
        if len(lines) < 2:
            return lines
        i, j = self.rng.sample(range(len(lines)), 2)
        copy = list(lines)
        copy[i], copy[j] = copy[j], copy[i]
        return copy

    def new_line(self) -> str:
        if self.templates and self.rng.random() < 0.55:
            return self.rng.choice(self.templates)
        words = self.pools.commands or self.pools.words
        head = self.rng.choice(words)
        arity = self.rng.choice([0, 1, 2, 3, 4, 5, 6])
        return _join([head, *(self.pools.token(self.rng) for _ in range(arity))], self.delimiter)

    def insert_line(self, lines: list[str]) -> list[str]:
        at = self.rng.randint(0, len(lines))
        return [*lines[:at], self.new_line(), *lines[at:]]

    def change_command(self, lines: list[str]) -> list[str]:
        index = self._pick(lines)
        if index is None or not self.pools.commands:
            return self.insert_line(lines)
        tokens = _tokens(lines[index], self.delimiter) or [""]
        tokens[0] = self.rng.choice(self.pools.commands)
        return [*lines[:index], _join(tokens, self.delimiter), *lines[index + 1 :]]

    def blank_or_comment(self, lines: list[str]) -> list[str]:
        at = self.rng.randint(0, len(lines))
        return [*lines[:at], self.rng.choice(["", "   ", "# note", "\t"]), *lines[at:]]

    def mutate_args(self, args: tuple[str, ...]) -> tuple[str, ...]:
        if not self.hints.uses_args:
            return args
        roll = self.rng.random()
        items = list(args)
        if roll < 0.4 or not items:
            items.append(self.pools.token(self.rng))
        elif roll < 0.7:
            items[self.rng.randrange(len(items))] = self.pools.token(self.rng)
        else:
            items.pop(self.rng.randrange(len(items)))
        return tuple(items[:6])

    # --- сборка ---------------------------------------------------------------------------------------

    def mutate(self, parent: TestInput, other: TestInput | None = None) -> TestInput:
        lines = split_lines(parent.stdin)
        args = parent.args
        operators = [
            self.replace_token,
            self.replace_token,
            self.tweak_number,
            self.insert_line,
            self.insert_line,
            self.change_command,
            self.duplicate_line,
            self.delete_line,
            self.swap_lines,
            self.blank_or_comment,
        ]
        for _ in range(self.rng.choice([1, 1, 2, 3])):
            if not self.hints.reads_stdin:
                break
            lines = self.rng.choice(operators)(lines)
        if other is not None and self.rng.random() < 0.15:
            extra = split_lines(other.stdin)
            if extra:
                at = self.rng.randint(0, len(lines))
                lines = [*lines[:at], *extra[: self.rng.randint(1, 4)], *lines[at:]]
        if self.rng.random() < 0.3:
            args = self.mutate_args(args)
        lines = lines[:MAX_LINES]
        text = join_lines(lines)
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            text = parent.stdin
        return TestInput(text, args, "mutant")
