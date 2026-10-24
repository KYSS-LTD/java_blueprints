"""RunManager и SSE без JDK (S3.6): очередь, отмена, replay, восстановление после перезапуска.

Обработчик ``run`` подменяется управляемым: он ждёт сигнал, поэтому порядок событий детерминирован.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlmodel import Session

from app.db.models import Run
from app.jobs import run_manager
from app.jobs.run_manager import JobContext, Outcome
from app.schemas import verify as schemas
from tests.integration.conftest import read_sse, wait_run


class Gate:
    """Управляемый обработчик: каждая задача ждёт ``release`` и отвечает на отмену."""

    def __init__(self) -> None:
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.running = 0
        self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, ctx: JobContext) -> Outcome:
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        self.started.release()
        ctx.log("info", "старт")
        try:
            while not self.release.wait(0.01):
                if ctx.cancel.is_set():
                    return Outcome("cancelled", schemas.RunSummary(message="отменён"))
            ctx.log("stdout", "готово\n")
            item = schemas.RunItem(name="fake", verdict="PASS", exit_code=0, duration_ms=1)
            ctx.emit(
                schemas.ItemEvent(data=schemas.ItemData(name="fake", verdict="PASS", duration_ms=1))
            )
            return Outcome("done", schemas.RunSummary(passed=1, total=1), [item])
        finally:
            with self.lock:
                self.running -= 1


@pytest.fixture
def gate(client) -> Gate:
    fake = Gate()
    client.app.state.run_manager.handlers["run"] = fake
    return fake


def start(client, project, **body) -> int:
    response = client.post(f"/api/projects/{project['id']}/runs", json={"kind": "run", **body})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return response.json()["run_id"]


def names(events) -> list[str]:
    return [e["event"] for e in events]


def test_events_stream_in_order_and_late_subscribers_get_replay(client, make_project, gate):
    project = make_project()
    run_id = start(client, project)
    assert gate.started.acquire(timeout=5)
    gate.release.set()
    info = wait_run(client, run_id, 10)
    assert info["status"] == "done"
    assert info["summary"]["passed"] == 1
    assert [i["name"] for i in info["items"]] == ["fake"]

    first = read_sse(client, run_id)
    assert names(first) == ["status", "status", "log", "log", "item", "result", "status", "done"]
    assert [e["data"]["status"] for e in first if e["event"] == "status"] == [
        "queued",
        "running",
        "done",
    ]
    assert [e["id"] for e in first] == [str(i) for i in range(len(first))]

    late = read_sse(client, run_id)  # поздний подписчик получает тот же поток с начала
    assert late == first

    resumed = read_sse(client, run_id, headers={"Last-Event-ID": "4"})
    assert resumed == first[5:]


def test_subscriber_connected_before_the_end_gets_the_whole_stream(client, make_project, gate):
    project = make_project()
    run_id = start(client, project)
    assert gate.started.acquire(timeout=5)
    threading.Timer(0.2, gate.release.set).start()
    events = read_sse(client, run_id)  # подключаемся, пока запуск ещё идёт
    assert names(events)[-1] == "done"
    assert "item" in names(events)


def test_a_slow_finish_does_not_cut_the_stream_before_done(client, make_project, gate, monkeypatch):
    """Запуск считается завершённым только после ``done``: подписчик не должен потерять итог."""
    project = make_project()
    run_id = start(client, project)
    assert gate.started.acquire(timeout=5)
    manager = client.app.state.run_manager
    seen: dict[str, bool] = {}
    real = run_manager.runs_service.finish

    def slow_finish(*args, **kwargs):
        time.sleep(0.4)  # запись итога в БД; подписчик к этому моменту прочитал все прежние события
        seen["finished_while_writing"] = manager.states[run_id].finished
        return real(*args, **kwargs)

    monkeypatch.setattr(run_manager.runs_service, "finish", slow_finish)
    threading.Timer(0.2, gate.release.set).start()
    events = read_sse(client, run_id)
    assert seen["finished_while_writing"] is False
    assert names(events)[-3:] == ["result", "status", "done"]


def test_events_are_restored_from_the_database_when_memory_is_gone(client, make_project, gate):
    project = make_project()
    run_id = start(client, project)
    assert gate.started.acquire(timeout=5)
    gate.release.set()
    wait_run(client, run_id, 10)

    client.app.state.run_manager.states.clear()  # как после перезапуска сервера
    events = read_sse(client, run_id)
    assert names(events) == ["status", "item", "result", "status", "done"]
    assert events[-2]["data"]["status"] == "done"
    assert events[1]["data"]["name"] == "fake"


def test_unknown_run_is_404_for_status_events_and_cancel(client):
    assert client.get("/api/runs/404").status_code == 404
    assert client.get("/api/runs/404/events").status_code == 404
    assert client.post("/api/runs/404/cancel").status_code == 404


def test_cancel_stops_a_running_job_and_is_idempotent(client, make_project, gate):
    project = make_project()
    run_id = start(client, project)
    assert gate.started.acquire(timeout=5)

    response = client.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert names(read_sse(client, run_id))[-1] == "done"

    again = client.post(f"/api/runs/{run_id}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"


def test_parallelism_is_limited_and_the_rest_waits_in_the_queue(client, make_project, gate):
    manager = client.app.state.run_manager
    manager.max_parallel = 2
    manager._semaphore = None  # пересоздаётся с новым лимитом
    project = make_project()
    run_ids = [start(client, project) for _ in range(4)]

    assert gate.started.acquire(timeout=5)
    assert gate.started.acquire(timeout=5)
    time.sleep(0.2)
    assert gate.peak == 2
    states = sorted(client.get(f"/api/runs/{r}").json()["status"] for r in run_ids)
    assert states == ["queued", "queued", "running", "running"]

    gate.release.set()
    assert {wait_run(client, r, 10)["status"] for r in run_ids} == {"done"}
    assert gate.peak == 2


def test_cancelling_a_queued_run_finishes_it_without_running(client, make_project, gate):
    manager = client.app.state.run_manager
    manager.max_parallel = 1
    manager._semaphore = None
    project = make_project()
    running = start(client, project)
    queued = start(client, project)
    assert gate.started.acquire(timeout=5)

    response = client.post(f"/api/runs/{queued}/cancel")
    assert response.json()["status"] == "cancelled"
    assert response.json()["started_at"] is None

    gate.release.set()
    assert wait_run(client, running, 10)["status"] == "done"
    assert not gate.started.acquire(timeout=0.3)  # отменённый запуск так и не стартовал
    assert client.get(f"/api/runs/{queued}").json()["status"] == "cancelled"


def test_too_many_active_runs_is_429_busy(client, make_project, gate):
    manager = client.app.state.run_manager
    manager.max_parallel = 1
    manager.max_queue = 1
    manager._semaphore = None
    project = make_project()
    first, second = start(client, project), start(client, project)

    response = client.post(f"/api/projects/{project['id']}/runs", json={"kind": "run"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "BUSY"

    gate.release.set()
    wait_run(client, first, 10)
    wait_run(client, second, 10)
    assert start(client, project)  # место освободилось


def test_unknown_version_is_404_and_unknown_kind_is_400(client, make_project, gate):
    project = make_project()
    url = f"/api/projects/{project['id']}/runs"
    assert client.post(url, json={"kind": "run", "version_id": 9999}).status_code == 404
    assert client.post(url, json={"kind": "bogus"}).status_code == 400


def test_failing_handler_ends_in_error_status_with_error_event(client, make_project):
    def boom(ctx: JobContext) -> Outcome:
        raise RuntimeError("сломалось")

    client.app.state.run_manager.handlers["run"] = boom
    project = make_project()
    run_id = start(client, project)
    info = wait_run(client, run_id, 10)
    assert info["status"] == "error"
    assert "сломалось" in info["summary"]["message"]
    events = read_sse(client, run_id)
    assert [e["data"]["code"] for e in events if e["event"] == "error"] == ["INTERNAL"]
    assert names(events)[-1] == "done"


def test_history_lists_newest_first_per_project(client, make_project, gate):
    gate.release.set()
    first, other = make_project(name="a"), make_project(name="b")
    ids = [start(client, first), start(client, first)]
    start(client, other)
    for run_id in ids:
        wait_run(client, run_id, 10)
    runs = client.get(f"/api/projects/{first['id']}/runs").json()["runs"]
    assert [r["id"] for r in runs] == ids[::-1]
    assert len(client.get(f"/api/projects/{other['id']}/runs").json()["runs"]) == 1


def test_runs_left_active_by_a_previous_process_are_marked_interrupted(
    client, make_project, session: Session
):
    project = make_project()
    stale = Run(
        project_id=project["id"],
        version_id=project["head_version_id"],
        kind="run",
        status="running",
    )
    session.add(stale)
    session.commit()
    session.refresh(stale)

    assert client.app.state.run_manager.recover() == 1
    info = client.get(f"/api/runs/{stale.id}").json()
    assert info["status"] == "error"
    assert "перезапуск" in info["summary"]["message"]
    assert client.app.state.run_manager.recover() == 0
