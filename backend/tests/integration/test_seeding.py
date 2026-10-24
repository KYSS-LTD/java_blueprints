"""Демо-данные при старте (S3.9): ``JB_SEED_DEMO`` создаёт проект из ``samples/demo-orders``."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.db.models import Project, Scenario
from app.main import create_app
from app.services.seeding import seed_demo


def test_seed_creates_the_demo_project_once(session: Session, tmp_path):
    settings = Settings(data_dir=tmp_path)
    project = seed_demo(session, settings)
    assert project is not None
    assert project.name == "Demo: заказы"
    assert project.main_class == "Main"
    assert len(session.exec(select(Scenario)).all()) == 11

    assert seed_demo(session, settings) is None  # повторный старт не плодит проекты
    assert len(session.exec(select(Project)).all()) == 1


def test_seed_skips_a_database_that_already_has_projects(session: Session, tmp_path):
    session.add(Project(name="мой"))
    session.commit()
    assert seed_demo(session, Settings(data_dir=tmp_path)) is None
    assert [p.name for p in session.exec(select(Project)).all()] == ["мой"]


def test_missing_sample_does_not_break_startup(session: Session, tmp_path):
    settings = Settings(data_dir=tmp_path, samples_dir=tmp_path / "nowhere")
    assert seed_demo(session, settings) is None


def test_app_seeds_on_startup_when_enabled(engine, monkeypatch):
    monkeypatch.setenv("JB_SEED_DEMO", "1")
    monkeypatch.setenv("JB_SEED_PREPARE", "0")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            projects = client.get("/api/projects").json()["projects"]
            assert [p["name"] for p in projects] == ["Demo: заказы"]
            assert client.get("/api/health").status_code == 200
    finally:
        monkeypatch.delenv("JB_SEED_DEMO")
        get_settings.cache_clear()


def test_app_does_not_seed_by_default(client):
    assert client.get("/api/projects").json() == {"projects": []}


@pytest.mark.jdk
def test_seeded_demo_is_ready_with_baseline_and_green_gate(engine, monkeypatch):
    """«Готовый» демо-проект: после старта эталон записан, gate зелёный на всех сценариях."""
    monkeypatch.setenv("JB_SEED_DEMO", "1")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            deadline = time.monotonic() + 120
            gate = {}
            while time.monotonic() < deadline:
                [project] = client.get("/api/projects").json()["projects"]
                gate = client.get(f"/api/projects/{project['id']}").json()["gate"]
                if gate["status"] != "unknown" and gate["total"] is not None:
                    break
                time.sleep(0.5)
            assert gate["status"] == "passed" and gate["passed"] == gate["total"] == 11, gate
    finally:
        monkeypatch.delenv("JB_SEED_DEMO")
        get_settings.cache_clear()
