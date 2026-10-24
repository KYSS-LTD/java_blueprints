"""Вывод схемы ввода программы (S6.8, docs/07 § 7.1–7.2).

Из синтаксического дерева выводится, что и в каком порядке программа читает из ``System.in``:
последовательность чтений (``Scanner.next*``, ``BufferedReader.readLine``), циклы чтения (до
конца ввода или ``n`` раз, где ``n`` прочитано раньше), циклы команд (``while (hasNext) { switch …}``),
а из условий и ``switch`` — словарь констант (команды, граничные числа), который использует
генератор. Анализ эвристический и консервативный: чего не поняли — не придумываем, генератор в
таком случае работает по запасному пути с обратной связью по покрытию.

Обход идёт по ``main`` и вглубь по вызываемым методам проекта (до ``MAX_DEPTH``): ввод читается
не в ``main``, а в ``run(Scanner in)``, ``processCommand`` и т. п.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from tree_sitter import Node

from engine.java import cst
from engine.java.project import JavaProject
from engine.java.semantic import Context
from engine.java.symbols import Member, base_name

MAX_DEPTH = 6
MAX_WORDS = 80
MAX_NUMBERS = 60

READ_TYPES = {
    "next": "word",
    "nextBoolean": "word",
    "nextInt": "int",
    "nextShort": "int",
    "nextByte": "int",
    "nextLong": "long",
    "nextDouble": "double",
    "nextFloat": "double",
    "nextLine": "line",
    "readLine": "line",
    "read": "line",
}
HAS_METHODS = frozenset(
    {"hasNext", "hasNextInt", "hasNextLong", "hasNextDouble", "hasNextLine", "ready"}
)
READERS = frozenset({"Scanner", "BufferedReader", "Console", "InputStreamReader"})
PARSERS = {
    "parseInt": "int",
    "parseLong": "long",
    "parseDouble": "double",
    "parseFloat": "double",
    "parseShort": "int",
    "parseByte": "int",
}
_PASS_THROUGH = frozenset({"trim", "strip", "toLowerCase", "toUpperCase", "toString"})
_COMPARISONS = frozenset({"<", ">", "<=", ">=", "==", "!="})
_STRING_TESTS = frozenset(
    {"equals", "equalsIgnoreCase", "startsWith", "endsWith", "contains", "indexOf", "compareTo"}
)
_LITERAL_NUMBER = re.compile(r"^-?\d[\d_]*(\.\d+)?([eE][-+]?\d+)?[lLfFdD]?$")


@dataclass(slots=True)
class SchemaNode:
    kind: str  # token | line | repeat | command_loop
    type: str | None = None
    source: str | None = None
    count_ref: str | None = None
    labels: list[str] = field(default_factory=list)
    children: list[SchemaNode] = field(default_factory=list)
    var: str | None = None  # имя переменной, в которую прочитано значение (служебное)


@dataclass(slots=True)
class InputHints:
    """Всё, что известно о вводе программы."""

    reads_stdin: bool = False
    uses_args: bool = False
    inferred: bool = False
    schema: list[SchemaNode] = field(default_factory=list)
    words: list[str] = field(default_factory=list)  # константы-строки из сравнений и switch
    commands: list[str] = field(default_factory=list)  # метки циклов команд
    numbers: list[float] = field(default_factory=list)  # числа из сравнений и switch
    delimiter: str = " "
    line_oriented: bool = True

    @property
    def read_types(self) -> list[str]:
        found: list[str] = []

        def visit(nodes: list[SchemaNode]) -> None:
            for node in nodes:
                if node.type:
                    found.append(node.type)
                visit(node.children)

        visit(self.schema)
        return found


@dataclass(frozen=True, slots=True)
class _Event:
    position: int
    end: int
    kind: str  # read | call
    node: Node


class _Analyzer:
    def __init__(self, project: JavaProject) -> None:
        self.project = project
        self.words: dict[str, None] = {}
        self.numbers: dict[float, None] = {}
        self.constants: dict[str, str | float] = {}
        self.delimiters: dict[str, int] = {}
        self.uses_args = False
        self.reads_stdin = False
        self.sinks: list[list[str]] = []
        self.active: list[str] = []
        self._collect_constants()

    # --- словарь констант по всему проекту --------------------------------------------------------------

    def _collect_constants(self) -> None:
        for path in self.project.files:
            parsed = self.project.parsed(path)
            for node in cst.walk(parsed.root):
                if node.type == "field_declaration":
                    mods, _ = cst.modifiers_of(parsed, node)
                    if "final" not in mods:
                        continue
                    for decl in cst.children_of_type(node, "variable_declarator"):
                        value = decl.child_by_field_name("value")
                        name = cst.text_of(parsed, decl.child_by_field_name("name"))
                        literal = self._literal(parsed.text(value)) if value is not None else None
                        if name and literal is not None:
                            self.constants[name] = literal

    @staticmethod
    def _literal(text: str) -> str | float | None:
        text = text.strip()
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"' and "\\" not in text:
            return text[1:-1]
        if _LITERAL_NUMBER.match(text):
            try:
                return float(text.rstrip("lLfFdD").replace("_", ""))
            except ValueError:
                return None
        return None

    def _value_of(self, node: Node | None, ctx_text: Callable[[Node], str]) -> str | float | None:
        if node is None:
            return None
        kind = node.type
        if kind in {"string_literal", "decimal_integer_literal", "decimal_floating_point_literal"}:
            return self._literal(ctx_text(node))
        if kind == "unary_expression":
            inner = cst.named_children(node)
            value = self._value_of(inner[-1], ctx_text) if inner else None
            if isinstance(value, float) and ctx_text(node).lstrip().startswith("-"):
                return -value
            return value
        if kind == "identifier":
            return self.constants.get(ctx_text(node))
        if kind == "parenthesized_expression":
            inner = cst.named_children(node)
            return self._value_of(inner[0], ctx_text) if inner else None
        return None

    def vocabulary(self) -> None:
        """Строки и числа из сравнений, ``switch`` и проверок строк по всему проекту."""
        for path in self.project.files:
            parsed = self.project.parsed(path)
            text = parsed.text
            for node in cst.walk(parsed.root):
                kind = node.type
                if kind == "binary_expression":
                    operator = cst.text_of(parsed, node.child_by_field_name("operator"))
                    if operator in _COMPARISONS:
                        for side in ("left", "right"):
                            value = self._value_of(node.child_by_field_name(side), text)
                            self._remember(value)
                elif kind == "switch_label":
                    for child in cst.named_children(node):
                        if child.type in {
                            "identifier",
                            "string_literal",
                            "decimal_integer_literal",
                        }:
                            self._remember(self._value_of(child, text) or self._enum(child, text))
                elif kind == "method_invocation":
                    name = cst.text_of(parsed, node.child_by_field_name("name"))
                    if name in _STRING_TESTS:
                        target = node.child_by_field_name("object")
                        arguments = node.child_by_field_name("arguments")
                        for candidate in [
                            target,
                            *(cst.named_children(arguments) if arguments else []),
                        ]:
                            value = self._value_of(candidate, text)
                            if isinstance(value, str):
                                self._remember(value)
                    elif name == "split":
                        arguments = node.child_by_field_name("arguments")
                        args = cst.named_children(arguments) if arguments else []
                        value = self._value_of(args[0], text) if args else None
                        if isinstance(value, str):
                            self.delimiters[value] = self.delimiters.get(value, 0) + 1
                elif kind == "array_access":
                    array = node.child_by_field_name("array")
                    if array is not None and cst.text_of(parsed, array) == "args":
                        self.uses_args = True
                elif kind == "field_access" and cst.text_of(parsed, node) == "args.length":
                    self.uses_args = True

    @staticmethod
    def _enum(node: Node, text: Callable[[Node], str]) -> str | None:
        return text(node) if node.type == "identifier" else None

    def _remember(self, value: str | float | None) -> None:
        if isinstance(value, str):
            if value and len(value) <= 40 and len(self.words) < MAX_WORDS:
                self.words.setdefault(value)
        elif isinstance(value, float) and len(self.numbers) < MAX_NUMBERS:
            self.numbers.setdefault(value)

    # --- чтения ----------------------------------------------------------------------------------------

    def _is_reader(self, receiver: Node | None, ctx: Context) -> bool:
        if receiver is None:
            return False
        text = cst.squash(ctx.parsed.text(receiver))
        if text.startswith("System.in") or text == "System.console()":
            return True
        found = ctx.expr_type(receiver)
        return found in READERS

    def _read_type(self, call: Node, ctx: Context) -> str:
        """Тип чтения с учётом разбора: ``Integer.parseInt(in.nextLine().trim())`` → ``int``."""
        name = cst.text_of(ctx.parsed, call.child_by_field_name("name"))
        declared = READ_TYPES.get(name, "word")
        current = call
        while True:
            parent = current.parent
            if parent is None:
                return declared
            if parent.type == "method_invocation":
                obj = parent.child_by_field_name("object")
                pname = cst.text_of(ctx.parsed, parent.child_by_field_name("name"))
                if obj is not None and obj.id == current.id and pname in _PASS_THROUGH:
                    current = parent
                    continue
            if parent.type == "argument_list" and parent.parent is not None:
                outer = parent.parent
                if outer.type == "method_invocation":
                    pname = cst.text_of(ctx.parsed, outer.child_by_field_name("name"))
                    if pname in PARSERS:
                        return PARSERS[pname]
                    if pname == "valueOf":
                        owner = cst.text_of(ctx.parsed, outer.child_by_field_name("object"))
                        return {"Integer": "int", "Long": "long", "Double": "double"}.get(
                            owner, declared
                        )
            return declared

    def _events(self, expr: Node, ctx: Context) -> list[_Event]:
        found: list[_Event] = []
        for node in cst.walk(expr):
            if node.type != "method_invocation":
                continue
            name = cst.text_of(ctx.parsed, node.child_by_field_name("name"))
            receiver = node.child_by_field_name("object")
            if name in READ_TYPES and self._is_reader(receiver, ctx):
                found.append(_Event(node.start_byte, node.end_byte, "read", node))
            elif name not in HAS_METHODS and ctx.resolve_call(node):
                found.append(_Event(node.start_byte, node.end_byte, "call", node))
        found.sort(key=lambda e: (e.end, e.position))
        return found

    @staticmethod
    def _has_eof_check(expr: Node | None, ctx: Context) -> bool:
        if expr is None:
            return False
        for node in cst.walk(expr):
            if node.type == "method_invocation":
                name = cst.text_of(ctx.parsed, node.child_by_field_name("name"))
                if name in HAS_METHODS or name == "readLine":
                    return True
        return False

    # --- обход ----------------------------------------------------------------------------------------------

    def analyze_member(
        self, member: Member, depth: int, binding: dict[str, str]
    ) -> list[SchemaNode]:
        if depth > MAX_DEPTH or member.id in self.active or not member.has_body:
            return []
        ctx, declaration = self.project.context(member)
        if declaration is None:
            return []
        body = declaration.child_by_field_name("body")
        if body is None:
            return []
        self.active.append(member.id)
        try:
            return self.block(cst.named_children(body), ctx, depth, binding, {})
        finally:
            self.active.pop()

    def block(
        self,
        statements: list[Node],
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        out: list[SchemaNode] = []
        for statement in statements:
            out.extend(self.statement(statement, ctx, depth, binding, known))
        return out

    def statement(
        self,
        node: Node,
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        kind = node.type
        if kind in cst.COMMENT_TYPES:
            return []
        if kind == "block":
            return self.block(cst.named_children(node), ctx, depth, binding, known)
        if kind in {
            "expression_statement",
            "local_variable_declaration",
            "return_statement",
            "throw_statement",
            "yield_statement",
            "assert_statement",
        }:
            return self.simple(node, ctx, depth, binding, known)
        if kind == "if_statement":
            return self.branching(node, ctx, depth, binding, known)
        if kind in {"while_statement", "do_statement", "for_statement", "enhanced_for_statement"}:
            return self.loop(node, ctx, depth, binding, known)
        if kind == "switch_expression":
            return self.switch(node, ctx, depth, binding, known)
        if kind in {"try_statement", "try_with_resources_statement"}:
            nodes: list[SchemaNode] = []
            resources = cst.first_child_of_type(node, "resource_specification")
            if resources is not None:
                nodes += self.simple(resources, ctx, depth, binding, known)
            body = node.child_by_field_name("body")
            if body is not None:
                nodes += self.statement(body, ctx, depth, binding, known)
            return nodes
        if kind in {"labeled_statement", "synchronized_statement"}:
            nodes = []
            for child in cst.named_children(node):
                if child.type != "identifier":
                    nodes += self.statement(child, ctx, depth, binding, known)
            return nodes
        return []

    def simple(
        self,
        node: Node,
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        """Чтения и вызовы методов проекта внутри выражения в порядке вычисления."""
        out: list[SchemaNode] = []
        var = self._assigned_name(node, ctx)
        first_read = True
        for event in self._events(node, ctx):
            if event.kind == "read":
                call = event.node
                name = cst.text_of(ctx.parsed, call.child_by_field_name("name"))
                self.reads_stdin = True
                typ = self._read_type(call, ctx)
                row = ctx.parsed.lines.position(call.start_byte).line
                item = SchemaNode(
                    "line" if typ == "line" else "token",
                    typ,
                    f"{name}@L{row}",
                    var=var if first_read else None,
                )
                first_read = False
                if item.var:
                    known[item.var] = item
                out.append(item)
            else:
                out.extend(self.inline(event.node, ctx, depth, binding, known))
        return out

    @staticmethod
    def _assigned_name(node: Node, ctx: Context) -> str | None:
        if node.type == "local_variable_declaration":
            decls = cst.children_of_type(node, "variable_declarator")
            if len(decls) == 1:
                return cst.text_of(ctx.parsed, decls[0].child_by_field_name("name")) or None
        if node.type == "expression_statement":
            inner = cst.named_children(node)
            if inner and inner[0].type == "assignment_expression":
                left = inner[0].child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    return cst.text_of(ctx.parsed, left) or None
        return None

    def inline(
        self,
        call: Node,
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        targets = ctx.resolve_call(call)
        if not targets:
            return []
        callee = targets[0]
        arguments = call.child_by_field_name("arguments")
        passed = cst.named_children(arguments) if arguments is not None else []
        inner: dict[str, str] = {}
        for param, arg in zip(callee.params, passed, strict=False):
            if arg.type == "identifier":
                name = ctx.parsed.text(arg)
                inner[param.name] = binding.get(name, name)
        return self.analyze_member(callee, depth + 1, inner)

    def branching(
        self,
        node: Node,
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        condition = node.child_by_field_name("condition")
        out = self.simple(condition, ctx, depth, binding, known) if condition is not None else []
        self._note_equals(condition, ctx)
        branches: list[list[SchemaNode]] = []
        for field_name in ("consequence", "alternative"):
            branch = node.child_by_field_name(field_name)
            if branch is not None:
                branches.append(self.statement(branch, ctx, depth, binding, dict(known)))
        out.extend(next((b for b in branches if b), []))
        return out

    def switch(
        self,
        node: Node,
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        condition = node.child_by_field_name("condition")
        out = self.simple(condition, ctx, depth, binding, known) if condition is not None else []
        body = node.child_by_field_name("body")
        branches: list[list[SchemaNode]] = []
        for group in cst.named_children(body) if body is not None else []:
            for label in cst.children_of_type(group, "switch_label"):
                self._add_label(label, ctx)
            inner = [c for c in cst.named_children(group) if c.type != "switch_label"]
            branches.append(self.block(inner, ctx, depth, binding, dict(known)))
        out.extend(next((b for b in branches if b), []))
        return out

    def _add_label(self, label: Node, ctx: Context) -> None:
        for child in cst.named_children(label):
            text = cst.squash(ctx.parsed.text(child))
            value = self._literal(text) if child.type != "identifier" else text
            if isinstance(value, str):
                self._sink(value)
            elif isinstance(value, float):
                self._sink(str(int(value)) if value == int(value) else str(value))

    def _sink(self, label: str) -> None:
        for sink in self.sinks:
            if label not in sink:
                sink.append(label)

    def _note_equals(self, condition: Node | None, ctx: Context) -> None:
        if condition is None:
            return
        for node in cst.walk(condition):
            if node.type == "method_invocation":
                name = cst.text_of(ctx.parsed, node.child_by_field_name("name"))
                if name in {"equals", "equalsIgnoreCase"}:
                    arguments = node.child_by_field_name("arguments")
                    for candidate in [
                        node.child_by_field_name("object"),
                        *(cst.named_children(arguments) if arguments else []),
                    ]:
                        value = self._value_of(candidate, ctx.parsed.text)
                        if isinstance(value, str):
                            self._sink(value)

    def loop(
        self,
        node: Node,
        ctx: Context,
        depth: int,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> list[SchemaNode]:
        condition = node.child_by_field_name("condition")
        body = node.child_by_field_name("body")
        header: list[SchemaNode] = []
        count_ref: str | None = None
        eof = False
        forever = False
        if node.type in {"while_statement", "do_statement"}:
            eof = self._has_eof_check(condition, ctx)
            text = cst.squash(ctx.parsed.text(condition)) if condition is not None else ""
            forever = text in {"(true)", "true"}
            if not eof and condition is not None:
                header = self.simple(condition, ctx, depth, binding, known)
        elif node.type == "for_statement":
            count_ref = self._counter_bound(condition, ctx, binding, known)
            eof = condition is not None and self._has_eof_check(condition, ctx)
            forever = condition is None
        else:
            value = node.child_by_field_name("value")
            if value is not None:
                header = self.simple(value, ctx, depth, binding, known)

        sink: list[str] = []
        self.sinks.append(sink)
        try:
            children = (
                self.statement(body, ctx, depth, binding, dict(known)) if body is not None else []
            )
        finally:
            self.sinks.pop()
        if not children and not eof:
            return header
        if eof or forever:
            count_ref = "EOF" if eof else "команда выхода"
        for label in sink:  # метки видны и охватывающим циклам
            self._sink(label)
        if sink and (eof or forever):
            return [
                *header,
                SchemaNode("command_loop", count_ref=count_ref, labels=sink, children=children),
            ]
        if not children:
            return header
        return [*header, SchemaNode("repeat", count_ref=count_ref, children=children)]

    def _counter_bound(
        self,
        condition: Node | None,
        ctx: Context,
        binding: dict[str, str],
        known: dict[str, SchemaNode],
    ) -> str | None:
        """Граница ``for (int i = 0; i < n; i++)``: прочитанная переменная либо число."""
        if condition is None or condition.type != "binary_expression":
            return None
        operator = cst.text_of(ctx.parsed, condition.child_by_field_name("operator"))
        if operator not in {"<", "<="}:
            return None
        bound = condition.child_by_field_name("right")
        if bound is None:
            return None
        text = cst.squash(ctx.parsed.text(bound))
        if bound.type == "identifier":
            name = binding.get(text, text)
            return name if name in known or text in binding else None
        if _LITERAL_NUMBER.match(text):
            return text
        return None


def _main_member(project: JavaProject, main_class: str | None) -> Member | None:
    wanted = main_class.rsplit(".", 1)[-1] if main_class else None
    candidates: list[Member] = []
    for member in project.members():
        if member.kind != "method" or member.name != "main" or not member.is_static:
            continue
        if len(member.params) != 1 or "String" not in member.params[0].type:
            continue
        candidates.append(member)
    for member in candidates:
        if wanted is None or member.type_path.rsplit(".", 1)[-1] == wanted:
            return member
    return candidates[0] if candidates else None


def infer(project: JavaProject, main_class: str | None = None) -> InputHints:
    """Схема ввода и словарь констант проекта; ``inferred=False`` — прочитать структуру не удалось."""
    analyzer = _Analyzer(project)
    analyzer.vocabulary()
    main = _main_member(project, main_class)
    schema: list[SchemaNode] = []
    if main is not None:
        schema = analyzer.analyze_member(main, 0, {})
    reads = analyzer.reads_stdin or any(
        "System.in" in text or "System.console" in text for text in project.files.values()
    )
    commands: list[str] = []

    def collect(nodes: list[SchemaNode]) -> None:
        for node in nodes:
            for label in node.labels:
                if label not in commands:
                    commands.append(label)
            collect(node.children)

    collect(schema)
    delimiter = " "
    if analyzer.delimiters:
        best = max(analyzer.delimiters.items(), key=lambda kv: (kv[1], kv[0]))[0]
        delimiter = "," if best.startswith(",") else (";" if best.startswith(";") else " ")
    types = []

    def collect_types(nodes: list[SchemaNode]) -> None:
        for node in nodes:
            if node.type:
                types.append(node.type)
            collect_types(node.children)

    collect_types(schema)
    return InputHints(
        reads_stdin=reads,
        uses_args=analyzer.uses_args,
        inferred=bool(schema),
        schema=schema,
        words=list(analyzer.words),
        commands=commands,
        numbers=list(analyzer.numbers),
        delimiter=delimiter,
        line_oriented=not types or "line" in types,
    )


__all__ = ["InputHints", "SchemaNode", "base_name", "infer"]
