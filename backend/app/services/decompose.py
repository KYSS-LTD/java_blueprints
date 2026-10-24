"""Декомпозиция монолита через API: план, предпросмотр, кандидат-версия (S9.5–S9.8, docs/08 § 7)."""

from __future__ import annotations

from sqlmodel import Session

from app.config import Settings
from app.db.models import Project, Version
from app.errors import ApiError
from app.schemas import transform as schemas
from app.services import builds
from app.services import fixes as fixes_service
from app.services import versions as versions_service
from engine.java.project import JavaProject, content_hash_of
from engine.transform.base import FixProposal
from engine.transform.decompose.generate import DecomposeError, Generated, generate
from engine.transform.decompose.plan import Plan, PlanClass, PlanEdge, Stat, build_plan

SUMMARY = (
    "Методы кластеров переносятся в новые классы, состояние остаётся в ядре, вызовы переписываются."
)


def to_schema(plan: Plan) -> schemas.DecomposePlan:
    return schemas.DecomposePlan(
        classes=[
            schemas.DecomposeClass(
                name=c.name,
                role=c.role,
                file=c.file,
                members=c.members,
                lines=c.lines,
                reason=c.reason,  # type: ignore[arg-type]
            )
            for c in plan.classes
        ],
        edges=[
            schemas.DecomposeEdge(from_=e.from_, to=e.to, calls=e.calls, fields=e.fields)
            for e in plan.edges
        ],
        warnings=plan.warnings,
        metrics=schemas.DecomposeMetrics(
            before=schemas.DecomposeStat(lines=plan.before.lines, lcom4=plan.before.lcom4),
            after=schemas.DecomposeStat(lines=plan.after.lines, lcom4=plan.after.lcom4),
        ),
    )


def from_schema(plan: schemas.DecomposePlan) -> Plan:
    """План, возможно отредактированный пользователем; тип-монолит определяется по ядру."""
    core = next((c for c in plan.classes if c.role == "core"), None)
    if core is None:
        raise ApiError(400, "BAD_REQUEST", "в плане нет класса-ядра (role=core)")
    return Plan(
        type_id=f"{core.file}#{core.name}",
        classes=[
            PlanClass(c.name, c.role, c.file, list(c.members), c.lines, c.reason)
            for c in plan.classes
        ],
        edges=[PlanEdge(e.from_, e.to, e.calls, e.fields) for e in plan.edges],
        warnings=list(plan.warnings),
        before=Stat(plan.metrics.before.lines, plan.metrics.before.lcom4),
        after=Stat(plan.metrics.after.lines, plan.metrics.after.lcom4),
    )


def make_plan(java: JavaProject, options: schemas.DecomposeOptions | None) -> schemas.DecomposePlan:
    try:
        plan = build_plan(
            java,
            max_classes=options.max_classes if options else None,
            min_members=options.min_members if options else None,
            seed=options.seed if options else None,
        )
    except LookupError as error:
        raise ApiError(422, "PRECONDITION_FAILED", str(error)) from error
    return to_schema(plan)


def build_files(java: JavaProject, plan: schemas.DecomposePlan) -> Generated:
    try:
        return generate(java, from_schema(plan))
    except DecomposeError as error:
        raise ApiError(422, "PRECONDITION_FAILED", error.message) from error


def preview(
    java: JavaProject, settings: Settings, plan: schemas.DecomposePlan
) -> schemas.DecomposePreview:
    generated = build_files(java, plan)
    changed = generated.changed
    diff = versions_service.diff_files(
        {p: java.files[p] for p in changed if p in java.files},
        {p: generated.files[p] for p in changed},
    )
    warnings = list(plan.warnings) + generated.warnings
    try:  # компиляция результата: ошибку видно до применения
        content_hash = content_hash_of(generated.files)
        _, compiled = builds.prepare(settings, generated.files, content_hash)
        if not compiled.ok:
            first = next((d for d in compiled.diagnostics if d.severity == "error"), None)
            warnings.append(
                "результат не компилируется"
                + (f": {first.file}:{first.line}: {first.message}" if first else "")
            )
    except ApiError:
        warnings.append("компиляция результата не проверена: JDK недоступен")
    return schemas.DecomposePreview(plan=plan, diff=diff, warnings=warnings)


def candidate(
    session: Session,
    project: Project,
    head: Version,
    java: JavaProject,
    plan: schemas.DecomposePlan,
) -> Version:
    generated = build_files(java, plan)
    proposal = FixProposal("REF-04", "Декомпозиция монолита", SUMMARY, True, edits=[])
    version = fixes_service.create_candidate(
        session, project, head, proposal, generated.files, from_schema(plan).type_id
    )
    version.description = f"REF-04: декомпозиция на {len(plan.classes)} классов"
    version.meta = {**(version.meta or {}), "symbol_moves": generated.moves}
    session.add(version)
    session.commit()
    session.refresh(version)
    return version
