"""Преобразования: исправления, оптимизация, выделение метода, декомпозиция
(docs/08-api.md § 7, docs/06-transformations.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import Precondition
from app.schemas.common import ApiModel, FileDiff, SourceRange
from app.schemas.project import VersionInfo

TransformKind = Literal["OPT", "FIX", "CLEAN", "REF"]


class Effect(ApiModel):
    complexity_before: str | None = None
    complexity_after: str | None = None
    notes: str | None = None


class FixListItem(ApiModel):
    finding_id: str
    fix_id: str
    title: str
    behavior_preserving: bool
    available: bool


class FixList(ApiModel):
    version_id: int
    fixes: list[FixListItem]


class FixPreview(ApiModel):
    """Предпросмотр преобразования (TransformProposal): diff, эффект, предусловия."""

    fix_id: str
    finding_id: str | None = None
    title: str
    summary: str
    preconditions: list[Precondition]
    effect: Effect
    diff: list[FileDiff]
    behavior_preserving: bool


class FixApply(BaseModel):
    base_version_id: int
    params: dict[str, Any] | None = None


class FixPreviewRequest(BaseModel):
    version_id: int | None = None
    params: dict[str, Any] | None = None


class ApplyResult(ApiModel):
    """Создан кандидат-версия, запущен gate (``kind=gate``)."""

    candidate_version_id: int
    run_id: int


class OptimizeStep(ApiModel):
    step_id: str
    fix_id: str
    finding_id: str
    title: str
    effect: Effect
    behavior_preserving: bool


class OptimizePlan(ApiModel):
    version_id: int
    steps: list[OptimizeStep]
    effect: Effect  # итоговый эффект плана


class OptimizePlanRequest(BaseModel):
    version_id: int | None = None


class OptimizeApply(BaseModel):
    base_version_id: int
    step_ids: list[str] = Field(min_length=1)


class ExtractMethodTarget(BaseModel):
    """Выделяемый фрагмент: узлы блок-схемы или диапазон кода (ровно одно из полей)."""

    node_ids: list[str] | None = None
    range: SourceRange | None = None
    name: str | None = None


class ExtractMethodRequest(ExtractMethodTarget):
    """Предпросмотр: версия не обязательна, по умолчанию HEAD."""

    version_id: int | None = None


class ExtractMethodPreview(ApiModel):
    name: str
    signature: str
    code: str
    preconditions: list[Precondition]
    warnings: list[str]
    diff: list[FileDiff]


class ExtractMethodApply(ExtractMethodTarget):
    base_version_id: int


DecomposeRole = Literal["parser", "analyzer", "optimizer", "printer", "model", "util", "core"]


class DecomposeClass(ApiModel):
    name: str
    role: DecomposeRole
    file: str
    members: list[str]  # id методов и полей
    lines: int
    reason: str


class DecomposeEdge(ApiModel):
    from_: str = Field(alias="from")
    to: str
    calls: int
    fields: int

    model_config = ConfigDict(
        populate_by_name=True, json_schema_serialization_defaults_required=True
    )


class DecomposeStat(ApiModel):
    lines: int
    lcom4: float


class DecomposeMetrics(ApiModel):
    before: DecomposeStat
    after: DecomposeStat


class DecomposePlan(ApiModel):
    classes: list[DecomposeClass]
    edges: list[DecomposeEdge]
    warnings: list[str]
    metrics: DecomposeMetrics
    # члены, которые остаются в ядре, и причина: их нельзя переносить между классами
    pinned: dict[str, str]


class DecomposeOptions(BaseModel):
    max_classes: int | None = Field(default=None, ge=2, le=12)
    min_members: int | None = Field(default=None, ge=1)
    seed: int | None = None


class DecomposePlanRequest(BaseModel):
    version_id: int | None = None
    options: DecomposeOptions | None = None


class DecomposeEdit(BaseModel):
    """План после правок пользователя (перенос методов между классами, переименование)."""

    base_version_id: int
    plan: DecomposePlan


class DecomposePreview(ApiModel):
    plan: DecomposePlan
    diff: list[FileDiff]
    warnings: list[str]


class CandidateDecision(ApiModel):
    """Результат ``accept``/``reject`` кандидата."""

    version: VersionInfo
    head_version_id: int
