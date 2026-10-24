"""Проекты: создание из файлов и встроенных примеров, настройки, главный класс, удаление (S3.1–S3.2)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, delete, select

from app.config import Settings
from app.db.models import (
    Baseline,
    Project,
    Run,
    RunItem,
    Scenario,
    ScenarioSource,
    SourceFile,
    Version,
    VersionOrigin,
)
from app.errors import bad_request, not_found
from app.schemas.project import (
    GateSummary,
    ProjectCreated,
    ProjectDetail,
    ProjectPatch,
    ProjectSettings,
    ProjectSummary,
    SampleInfo,
)
from app.services import versions as versions_service
from engine.java.project import get_project


def get_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise not_found(f"проект {project_id} не найден", project_id=project_id)
    return project


# --- настройки -----------------------------------------------------------------------------------


def default_settings(settings: Settings) -> dict[str, Any]:
    return {
        "run_timeout_s": settings.run_timeout_s,
        "compile_timeout_s": settings.compile_timeout_s,
        "output_limit_bytes": settings.output_limit_bytes,
        "jvm_xmx": settings.jvm_xmx,
        "locale": "en_US",
        "timezone": "UTC",
        "rule_thresholds": {},
        "gate_min_scenarios": 3,
        "gate_min_coverage": 0.6,
    }


def project_settings(project: Project, settings: Settings) -> ProjectSettings:
    return ProjectSettings.model_validate(
        {**default_settings(settings), **(project.settings or {})}
    )


# --- создание ------------------------------------------------------------------------------------------


def create_project(
    session: Session,
    settings: Settings,
    name: str,
    files: Mapping[str, str],
    scenarios: list[dict[str, Any]] | None = None,
) -> Project:
    """Проект с версией 1 «Оригинал», определённым главным классом и (по желанию) сценариями."""
    clean = name.strip()
    if not clean:
        raise bad_request("у проекта должно быть имя")
    java = get_project(files)
    mains = java.main_classes()
    project = Project(name=clean, main_class=mains[0] if mains else None, settings={})
    session.add(project)
    session.flush()
    version = versions_service.create_version(
        session, project, files, origin=VersionOrigin.UPLOAD, description="Оригинал", parent_id=None
    )
    project.original_version_id = version.id
    project.baseline_version_id = version.id
    session.add(project)
    for item in scenarios or []:
        session.add(
            Scenario(
                project_id=project.id or 0,
                name=str(item["name"]),
                description=str(item.get("description", "")),
                stdin=str(item.get("stdin", "")),
                args=[str(a) for a in item.get("args", [])],
                source=ScenarioSource.IMPORTED.value,
                expected_stdout=item.get("expected_stdout"),
                expected_exit_code=item.get("expected_exit_code"),
            )
        )
    session.commit()
    session.refresh(project)
    return project


def created_response(session: Session, project: Project) -> ProjectCreated:
    version = versions_service.head_version(session, project)
    files = versions_service.version_files(session, version.id or 0)
    java = get_project(files)
    return ProjectCreated(
        id=project.id or 0,
        name=project.name,
        head_version_id=version.id or 0,
        original_version_id=project.original_version_id or version.id or 0,
        main_class=project.main_class,
        main_class_candidates=java.main_classes(),
        syntax_errors=versions_service.syntax_issues(java),
        files=versions_service.file_entries(files),
    )


def summary(session: Session, project: Project) -> ProjectSummary:
    count = len(
        session.exec(
            select(SourceFile.id).where(SourceFile.version_id == project.head_version_id)
        ).all()
    )
    return ProjectSummary(
        id=project.id or 0,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
        head_version_id=project.head_version_id or 0,
        main_class=project.main_class,
        files_count=count,
    )


def _gate_summary(session: Session, head: Version) -> GateSummary:
    """Статус поведения HEAD вместе со счётчиками последнего gate («✓ поведение совпадает (11/11)»)."""
    summary: dict[str, Any] = {}
    if head.gate_run_id is not None:
        run = session.get(Run, head.gate_run_id)
        summary = dict(run.summary or {}) if run is not None else {}
    return GateSummary(
        status=head.gate_status,  # type: ignore[arg-type]
        passed=summary.get("passed"),
        total=summary.get("total"),
        coverage=summary.get("coverage"),
        run_id=head.gate_run_id,
    )


def detail(session: Session, settings: Settings, project: Project) -> ProjectDetail:
    head = versions_service.head_version(session, project)
    java = get_project(versions_service.version_files(session, head.id or 0))
    return ProjectDetail(
        id=project.id or 0,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
        main_class=project.main_class,
        main_class_candidates=java.main_classes(),
        head_version_id=head.id or 0,
        original_version_id=project.original_version_id or head.id or 0,
        baseline_version_id=project.baseline_version_id,
        settings=project_settings(project, settings),
        gate=_gate_summary(session, head),
    )


def patch(session: Session, settings: Settings, project: Project, payload: ProjectPatch) -> Project:
    if payload.name is not None:
        if not payload.name.strip():
            raise bad_request("у проекта должно быть имя")
        project.name = payload.name.strip()
    if payload.main_class is not None:
        head = versions_service.head_version(session, project)
        candidates = get_project(
            versions_service.version_files(session, head.id or 0)
        ).main_classes()
        if payload.main_class not in candidates:
            raise bad_request(
                f"{payload.main_class} не главный класс", main_class_candidates=candidates
            )
        project.main_class = payload.main_class
    if payload.settings is not None:
        merged = {**(project.settings or {}), **payload.settings.model_dump(exclude_none=True)}
        project.settings = merged
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def delete_project(session: Session, project: Project) -> None:
    project_id = project.id or 0
    version_ids = [
        v for v in session.exec(select(Version.id).where(Version.project_id == project_id)).all()
    ]
    run_ids = [r for r in session.exec(select(Run.id).where(Run.project_id == project_id)).all()]
    if run_ids:
        session.exec(delete(RunItem).where(col(RunItem.run_id).in_(run_ids)))  # type: ignore[call-overload]
    session.exec(delete(Baseline).where(Baseline.project_id == project_id))  # type: ignore[call-overload]
    session.exec(delete(Run).where(Run.project_id == project_id))  # type: ignore[call-overload]
    session.exec(delete(Scenario).where(Scenario.project_id == project_id))  # type: ignore[call-overload]
    if version_ids:
        session.exec(delete(SourceFile).where(col(SourceFile.version_id).in_(version_ids)))  # type: ignore[call-overload]
    session.exec(delete(Version).where(Version.project_id == project_id))  # type: ignore[call-overload]
    session.delete(project)
    session.commit()


# --- встроенные примеры ----------------------------------------------------------------------------


def _sample_dirs(samples_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for name in ("demo-orders", "analyzer-pairs"):
        if (samples_dir / name).is_dir():
            found[name] = samples_dir / name
    monoliths = samples_dir / "monoliths"
    if monoliths.is_dir():
        for path in sorted(monoliths.iterdir()):
            if path.is_dir():
                found[path.name] = path
    return found


def _java_files(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.java"))
        if "scenarios" not in p.relative_to(root).parts
    }


def list_samples(settings: Settings) -> list[SampleInfo]:
    result: list[SampleInfo] = []
    for sample_id, root in _sample_dirs(settings.samples_dir).items():
        readme = root / "README.md"
        text = readme.read_text(encoding="utf-8").strip().splitlines() if readme.exists() else []
        description = text[0].strip() if text else sample_id
        lines = sum(len(t.splitlines()) for t in _java_files(root).values())
        result.append(
            SampleInfo(id=sample_id, name=sample_id, description=description, lines=lines)
        )
    return result


def load_sample(settings: Settings, sample_id: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    root = _sample_dirs(settings.samples_dir).get(sample_id)
    if root is None:
        raise not_found(f"пример {sample_id} не найден", sample_id=sample_id)
    scenarios = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((root / "scenarios").glob("*.json"))
    ]
    return _java_files(root), scenarios
