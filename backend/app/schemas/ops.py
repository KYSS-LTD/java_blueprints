"""Операции над графом и палитра блоков (docs/08-api.md § 5, docs/04-code-graph-model.md § 5.2)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel, FileDiff, SourceRange, SyntaxIssue
from app.schemas.graph import FlowGraph, NodeKind
from app.schemas.project import VersionInfo

Fields = dict[str, Any]
"""Поля формы блока (см. палитру): значения проверяются как выражение, тип или идентификатор."""


class InsertNode(BaseModel):
    op: Literal["insert_node"] = "insert_node"
    parent_id: str
    slot: str  # body, then, else, try, catch:<n>, finally, case:<n>, …
    index: int = Field(ge=0)
    kind: NodeKind
    fields: Fields = {}


class UpdateNode(BaseModel):
    op: Literal["update_node"] = "update_node"
    node_id: str
    fields: Fields


class DeleteNode(BaseModel):
    op: Literal["delete_node"] = "delete_node"
    node_id: str
    mode: Literal["with_body", "unwrap"] = "with_body"


class MoveNode(BaseModel):
    op: Literal["move_node"] = "move_node"
    node_id: str
    new_parent_id: str
    slot: str
    index: int = Field(ge=0)


class DuplicateNode(BaseModel):
    op: Literal["duplicate_node"] = "duplicate_node"
    node_id: str


class WrapNode(BaseModel):
    op: Literal["wrap_node"] = "wrap_node"
    node_ids: list[str] = Field(min_length=1)
    wrapper: Literal["if", "try", "for", "while", "block"]
    fields: Fields = {}


class UnwrapNode(BaseModel):
    op: Literal["unwrap_node"] = "unwrap_node"
    node_id: str


class AddBranch(BaseModel):
    op: Literal["add_branch"] = "add_branch"
    node_id: str
    branch: Literal["else", "else_if", "catch", "finally", "case", "default"]
    fields: Fields = {}


class RemoveBranch(BaseModel):
    op: Literal["remove_branch"] = "remove_branch"
    node_id: str
    branch_id: str


class RenameSymbol(BaseModel):
    op: Literal["rename_symbol"] = "rename_symbol"
    symbol_id: str
    new_name: str


class AddMember(BaseModel):
    op: Literal["add_member"] = "add_member"
    type_id: str
    kind: Literal["field", "method", "constructor", "enum_constant", "record_component"]
    fields: Fields = {}


class UpdateMember(BaseModel):
    op: Literal["update_member"] = "update_member"
    member_id: str
    fields: Fields


class DeleteMember(BaseModel):
    op: Literal["delete_member"] = "delete_member"
    member_id: str


class ReplaceCode(BaseModel):
    """Прямая замена фрагмента текстом: узел или диапазон (ровно одно из полей)."""

    op: Literal["replace_code"] = "replace_code"
    node_id: str | None = None
    range: SourceRange | None = None
    text: str


GraphOp = Annotated[
    InsertNode
    | UpdateNode
    | DeleteNode
    | MoveNode
    | DuplicateNode
    | WrapNode
    | UnwrapNode
    | AddBranch
    | RemoveBranch
    | RenameSymbol
    | AddMember
    | UpdateMember
    | DeleteMember
    | ReplaceCode,
    Field(discriminator="op"),
]


class OpsRequest(BaseModel):
    """Пакет операций; применяется атомарно."""

    base_version_id: int
    ops: list[GraphOp] = Field(min_length=1)
    description: str | None = None


class OpsResult(ApiModel):
    version: VersionInfo
    changed_files: list[str]
    focus_node_id: str | None = None
    id_map: dict[str, str]  # старый id → новый id для затронутых узлов
    flow: FlowGraph | None = None  # граф изменённого метода
    syntax_errors: list[SyntaxIssue]


class OpError(ApiModel):
    op_index: int
    code: Literal["INVALID_FRAGMENT", "PRECONDITION_FAILED", "NOT_FOUND", "UNSUPPORTED"]
    message: str
    field: str | None = None
    line: int | None = None
    column: int | None = None


class OpsValidation(ApiModel):
    """Сухая проверка пакета: ничего не сохраняется."""

    ok: bool
    errors: list[OpError]
    diff: list[FileDiff]


FieldType = Literal[
    "expression", "type", "identifier", "text", "boolean", "select", "modifiers", "params"
]


class PaletteField(ApiModel):
    name: str
    label: str
    type: FieldType
    required: bool = True
    default: str | bool | None = None
    options: list[str] = []  # для select


class PaletteBlock(ApiModel):
    kind: NodeKind
    title: str  # «Переменная», «Условие», …
    group: Literal["basic", "control", "advanced"]
    color: str  # токен цвета макета: violet, blue, orange, green, teal…
    icon: str  # имя иконки lucide-react
    fields: list[PaletteField]
    template: str  # образец результата: «int count = 0;»


class Palette(ApiModel):
    blocks: list[PaletteBlock]


class ExpressionCheck(BaseModel):
    kind: Literal["expression", "type", "identifier"]
    text: str
    context_node_id: str | None = None


class ExpressionIssue(ApiModel):
    message: str
    line: int | None = None
    column: int | None = None


class ExpressionResult(ApiModel):
    ok: bool
    error: ExpressionIssue | None = None
