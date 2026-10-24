"""Выделение метода через API: предпросмотр и кандидат-версия (S9.2, docs/08 § 7)."""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import Project, Version
from app.errors import ApiError
from app.schemas import analysis as analysis_schemas
from app.schemas import transform as schemas
from app.services import fixes as fixes_service
from app.services import versions as versions_service
from engine.edit.core import EditError
from engine.java.project import JavaProject
from engine.transform.base import Check, FixProposal
from engine.transform.dataflow import Flow
from engine.transform.extract_method import ExtractPlan, plan

SUMMARY = "Фрагмент заменяется вызовом нового метода: параметры, результат и throws выводятся автоматически."


def build(java: JavaProject, target: schemas.ExtractMethodTarget) -> ExtractPlan:
    """План выделения. Недопустимый выбор — план с невыполненным предусловием, неизвестный узел — 404."""
    try:
        return plan(
            java,
            target.node_ids,
            target.range.model_dump() if target.range else None,
            target.name,
        )
    except EditError as error:
        if error.code == "NOT_FOUND":
            raise ApiError(404, error.code, error.message, {"field": error.field}) from error
        refusal = Check("Допустимый выбор фрагмента", False, error.message)
        return ExtractPlan("", "", "", [], Flow(), [refusal], [], "")


def checks_of(result: ExtractPlan) -> list[analysis_schemas.Precondition]:
    return [
        analysis_schemas.Precondition(name=c.name, passed=c.passed, reason=c.reason)
        for c in result.checks
    ]


def preview(java: JavaProject, target: schemas.ExtractMethodTarget) -> schemas.ExtractMethodPreview:
    """Предпросмотр: при невыполненных предусловиях ответ `200` с причинами и пустым diff."""
    result = build(java, target)
    diff = []
    if result.available:
        updated = fixes_service.new_files(java, _proposal(result))
        changed = {p for p in updated if updated[p] != java.files.get(p)}
        diff = versions_service.diff_files(
            {p: java.files[p] for p in changed if p in java.files}, {p: updated[p] for p in changed}
        )
    return schemas.ExtractMethodPreview(
        name=result.name,
        signature=result.signature,
        code=result.code,
        preconditions=checks_of(result),
        warnings=result.warnings,
        diff=diff,
    )


def _proposal(result: ExtractPlan) -> FixProposal:
    return FixProposal(
        "REF-01",
        "Выделение метода",
        SUMMARY,
        True,
        checks=list(result.checks),
        edits=list(result.edits),
    )


def candidate(
    session: Session,
    project: Project,
    head: Version,
    java: JavaProject,
    target: schemas.ExtractMethodTarget,
) -> Version:
    result = build(java, target)
    if not result.available:
        failed = [c for c in result.checks if not c.passed]
        raise ApiError(
            422,
            "PRECONDITION_FAILED",
            (failed[0].reason if failed else None) or "выделить метод нельзя",
            {
                "preconditions": [
                    {"name": c.name, "passed": c.passed, "reason": c.reason} for c in result.checks
                ]
            },
        )
    proposal = _proposal(result)
    files = fixes_service.new_files(java, proposal)
    node = (target.node_ids or [result.member_id])[0]
    version = fixes_service.create_candidate(session, project, head, proposal, files, node)
    version.description = f"REF-01: выделен метод {result.name}"
    session.add(version)
    session.commit()
    session.refresh(version)
    return version
