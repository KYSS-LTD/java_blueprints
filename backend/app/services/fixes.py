"""Автоисправления через API: предпросмотр, кандидат-версия, gate, accept/reject (S8, docs/08 § 7)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from app.config import Settings
from app.db.models import Project, Version, VersionOrigin, VersionStatus
from app.errors import ApiError, not_found, version_conflict
from app.schemas import analysis as analysis_schemas
from app.schemas import transform as schemas
from app.services import analysis as analysis_service
from app.services import versions as versions_service
from engine.analysis.model import Finding
from engine.edit.core import EditError
from engine.java.project import JavaProject
from engine.transform.base import FixProposal, apply_proposal
from engine.transform.fixes import propose


def find(java: JavaProject, project: Project, settings: Settings, finding_id: str) -> Finding:
    for item in analysis_service.run(java, project, settings).findings:
        if item.id == finding_id:
            return item
    raise not_found(f"находка {finding_id} не найдена", finding_id=finding_id)


def proposal_for(java: JavaProject, finding: Finding) -> FixProposal:
    proposal = propose(java, finding)
    if proposal is None:
        raise ApiError(
            422, "PRECONDITION_FAILED", f"для правила {finding.rule_id} нет автоисправления"
        )
    if not proposal.available:
        reasons = [c for c in proposal.failed()]
        raise ApiError(
            422,
            "PRECONDITION_FAILED",
            reasons[0].reason or "предусловия исправления не выполнены",
            {
                "fix_id": proposal.fix_id,
                "preconditions": [
                    {"name": c.name, "passed": c.passed, "reason": c.reason}
                    for c in proposal.checks
                ],
            },
        )
    return proposal


def new_files(java: JavaProject, proposal: FixProposal) -> dict[str, str]:
    try:
        return apply_proposal(java, proposal)
    except EditError as error:
        raise ApiError(422, "PRECONDITION_FAILED", error.message) from error


def listing(
    java: JavaProject, project: Project, settings: Settings, version: Version
) -> schemas.FixList:
    items = []
    for item in analysis_service.run(java, project, settings).findings:
        if item.fix is None:
            continue
        proposal = propose(java, item)
        title = proposal.title if proposal is not None else item.fix.fix_id
        items.append(
            schemas.FixListItem(
                finding_id=item.id,
                fix_id=item.fix.fix_id,
                title=title,
                behavior_preserving=item.fix.behavior_preserving,
                available=item.fix.available,
            )
        )
    return schemas.FixList(version_id=version.id or 0, fixes=items)


def preview(
    java: JavaProject, project: Project, settings: Settings, finding_id: str
) -> schemas.FixPreview:
    finding = find(java, project, settings, finding_id)
    proposal = proposal_for(java, finding)
    updated = new_files(java, proposal)
    changed = {p for p in updated if updated[p] != java.files.get(p)}
    diff = versions_service.diff_files(
        {p: java.files[p] for p in changed if p in java.files}, {p: updated[p] for p in changed}
    )
    complexity = finding.complexity
    return schemas.FixPreview(
        fix_id=proposal.fix_id,
        finding_id=finding.id,
        title=proposal.title,
        summary=proposal.summary,
        preconditions=[
            analysis_schemas.Precondition(name=c.name, passed=c.passed, reason=c.reason)
            for c in proposal.checks
        ],
        effect=schemas.Effect(
            complexity_before=complexity.current if complexity else None,
            complexity_after=complexity.after if complexity else None,
            notes=proposal.summary,
        ),
        diff=diff,
        behavior_preserving=proposal.behavior_preserving,
    )


def create_candidate(
    session: Session,
    project: Project,
    head: Version,
    proposal: FixProposal,
    files: dict[str, str],
    finding_id: str,
) -> Version:
    origin = {
        "OPT": VersionOrigin.OPTIMIZE,
        "REF-01": VersionOrigin.EXTRACT_METHOD,
        "REF-04": VersionOrigin.DECOMPOSE,
    }.get(
        proposal.fix_id[:3] if proposal.fix_id.startswith("OPT") else proposal.fix_id,
        VersionOrigin.AUTOFIX,
    )
    return versions_service.create_version(
        session,
        project,
        files,
        origin=origin,
        description=f"{proposal.fix_id}: {proposal.title}",
        parent_id=head.id,
        status=VersionStatus.CANDIDATE,
        meta={
            "fix_id": proposal.fix_id,
            "finding_id": finding_id,
            "behavior_preserving": proposal.behavior_preserving,
        },
        make_head=False,
    )


# --- кандидаты -------------------------------------------------------------------------------------------------------


def _candidate(session: Session, project: Project, version_id: int) -> Version:
    version = versions_service.get_version(session, project, version_id)
    if version.status != VersionStatus.CANDIDATE.value:
        raise ApiError(
            422, "PRECONDITION_FAILED", f"версия {version_id} не кандидат (статус {version.status})"
        )
    return version


def promote(session: Session, project: Project, version: Version) -> None:
    version.status = VersionStatus.ACTIVE.value
    project.head_version_id = version.id
    project.updated_at = datetime.now(UTC)
    session.add_all([version, project])
    session.commit()


def accept(session: Session, project: Project, version_id: int) -> schemas.CandidateDecision:
    version = _candidate(session, project, version_id)
    if version.parent_id != project.head_version_id:
        raise version_conflict(
            "HEAD проекта изменился после создания кандидата",
            head_version_id=project.head_version_id,
            parent_id=version.parent_id,
        )
    promote(session, project, version)
    session.refresh(version)
    return schemas.CandidateDecision(
        version=versions_service.version_info(version), head_version_id=project.head_version_id or 0
    )


def reject(session: Session, project: Project, version_id: int) -> schemas.CandidateDecision:
    version = _candidate(session, project, version_id)
    version.status = VersionStatus.REJECTED.value
    session.add(version)
    session.commit()
    session.refresh(version)
    return schemas.CandidateDecision(
        version=versions_service.version_info(version), head_version_id=project.head_version_id or 0
    )
