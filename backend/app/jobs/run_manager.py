"""RunManager: фоновые запуски, очередь по числу JVM, буфер событий для SSE, отмена (S3.6).

Задача — корутина, ждущая семафор и выполняющая обработчик вида запуска в потоке
(``asyncio.to_thread``): запуск Java синхронный и не зависит от политики цикла событий.
События пишутся в буфер запуска; подписчик SSE читает буфер с нужной позиции, поэтому поздний
подписчик получает replay с начала.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel
from sqlmodel import Session
from sse_starlette import ServerSentEvent

from app.config import Settings
from app.db.models import Project, RunStatus
from app.db.session import get_engine
from app.errors import ApiError, bad_request, busy
from app.schemas import verify as schemas
from app.services import runs as runs_service

log = logging.getLogger("app.jobs")

POLL_S = 0.05
KEEP_STATES = 200


class SupportsEvent(Protocol):
    event: str
    data: BaseModel


@dataclass(slots=True)
class Outcome:
    """Итог обработчика: статус запуска, сводка и элементы (по сценариям или один)."""

    status: str
    summary: schemas.RunSummary
    items: list[schemas.RunItem] = field(default_factory=list)


@dataclass(slots=True)
class RunState:
    run_id: int
    kind: str
    cancel: threading.Event = field(default_factory=threading.Event)
    events: list[tuple[str, str]] = field(default_factory=list)
    finished: bool = False
    status: str = RunStatus.QUEUED.value
    candidate: str | None = (
        None  # судьба версии-кандидата после gate: promoted | rejected | candidate | stale
    )
    _claim: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: SupportsEvent) -> None:
        self.events.append((event.event, event.data.model_dump_json()))

    def claim_finish(self) -> bool:
        """Только один путь (обработчик или отмена очереди) имеет право завершить запуск."""
        with self._claim:
            if self.finished:
                return False
            self.finished = True
            return True


@dataclass(slots=True)
class JobContext:
    """Всё, что нужно обработчику вида запуска."""

    state: RunState
    project_id: int
    version_id: int
    request: schemas.RunRequest
    settings: Settings
    main_class: str | None
    project_settings: dict[str, Any]

    @property
    def cancel(self) -> threading.Event:
        return self.state.cancel

    def emit(self, event: SupportsEvent) -> None:
        self.state.emit(event)

    def log(self, stream: str, text: str) -> None:
        if text:
            self.emit(schemas.LogEvent(data=schemas.LogData(stream=stream, text=text)))  # type: ignore[arg-type]


Handler = Callable[[JobContext], Outcome]


class RunManager:
    def __init__(
        self,
        settings: Settings,
        handlers: dict[str, Handler],
        *,
        max_parallel: int | None = None,
        max_queue: int | None = None,
    ) -> None:
        self.settings = settings
        self.handlers = handlers
        self.max_parallel = max_parallel or settings.max_parallel_runs
        self.max_queue = max_queue if max_queue is not None else self.max_parallel * 8
        self.states: OrderedDict[int, RunState] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore: asyncio.Semaphore | None = None

    # --- запуск ---------------------------------------------------------------------------------

    def _gate(self) -> asyncio.Semaphore:
        if self._semaphore is None:  # создаётся в цикле событий приложения
            self._semaphore = asyncio.Semaphore(self.max_parallel)
        return self._semaphore

    def active(self) -> int:
        return sum(1 for s in self.states.values() if not s.finished)

    async def submit(self, project: Project, request: schemas.RunRequest, version_id: int) -> int:
        """Создаёт запись запуска и ставит задачу в очередь; возвращает ``run_id``."""
        if request.kind not in self.handlers:
            raise bad_request(f"вид запуска {request.kind} не поддерживается")
        if self.active() >= self.max_parallel + self.max_queue:
            raise busy("слишком много активных запусков, повторите позже")
        run_id = await asyncio.to_thread(
            self._create_row, project.id or 0, request.kind, version_id
        )
        state = RunState(run_id, request.kind)
        state.emit(schemas.StatusEvent(data=schemas.StatusData(status="queued")))
        self._remember(state)
        context = JobContext(
            state=state,
            project_id=project.id or 0,
            version_id=version_id,
            request=request,
            settings=self.settings,
            main_class=project.main_class,
            project_settings=dict(project.settings or {}),
        )
        task = asyncio.get_running_loop().create_task(self._execute(context))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run_id

    @staticmethod
    def _create_row(project_id: int, kind: str, version_id: int) -> int:
        with Session(get_engine()) as session:
            return runs_service.create(session, project_id, kind, version_id).id or 0

    def _remember(self, state: RunState) -> None:
        self.states[state.run_id] = state
        while len(self.states) > KEEP_STATES:
            oldest = next(iter(self.states))
            if not self.states[oldest].finished:
                break
            del self.states[oldest]

    async def _execute(self, context: JobContext) -> None:
        state = context.state
        async with self._gate():
            if state.finished:  # отменён в очереди
                return
            outcome: Outcome
            try:
                await asyncio.to_thread(self._start, state)
                outcome = await asyncio.to_thread(self.handlers[state.kind], context)
            except ApiError as exc:
                log.warning("run %s failed: %s", state.run_id, exc.message)
                state.emit(
                    schemas.ErrorEvent(data=schemas.ErrorData(code=exc.code, message=exc.message))
                )
                outcome = Outcome(RunStatus.ERROR.value, schemas.RunSummary(message=exc.message))
            except Exception as exc:  # noqa: BLE001 - любая ошибка обработчика не должна терять запуск
                log.exception("run %s crashed", state.run_id)
                message = f"{type(exc).__name__}: {exc}"
                state.emit(
                    schemas.ErrorEvent(data=schemas.ErrorData(code="INTERNAL", message=message))
                )
                outcome = Outcome(RunStatus.ERROR.value, schemas.RunSummary(message=message))
            if state.cancel.is_set() and outcome.status in {"done", "failed"}:
                outcome.status = RunStatus.CANCELLED.value
            await asyncio.to_thread(self._finish, state, outcome)

    def _start(self, state: RunState) -> None:
        state.status = RunStatus.RUNNING.value
        state.emit(schemas.StatusEvent(data=schemas.StatusData(status="running")))
        with Session(get_engine()) as session:
            runs_service.mark_started(session, state.run_id)

    def _finish(self, state: RunState, outcome: Outcome) -> None:
        if not state.claim_finish():
            return
        with Session(get_engine()) as session:
            runs_service.finish(
                session, state.run_id, outcome.status, outcome.summary, outcome.items
            )
        state.status = outcome.status
        state.emit(schemas.ResultEvent(data=schemas.ResultData(summary=outcome.summary)))
        state.emit(schemas.StatusEvent(data=schemas.StatusData(status=outcome.status)))  # type: ignore[arg-type]
        state.emit(schemas.DoneEvent(data=schemas.DoneData()))

    # --- отмена ----------------------------------------------------------------------------------

    def cancel(self, run_id: int) -> bool:
        """Просит остановить запуск. ``False`` — в памяти его нет или он уже завершён."""
        state = self.states.get(run_id)
        if state is None or state.finished:
            return False
        state.cancel.set()
        if state.status == RunStatus.QUEUED.value:
            self._finish(
                state, Outcome(RunStatus.CANCELLED.value, schemas.RunSummary(message="отменён"))
            )
        return True

    async def wait_finished(self, run_id: int, timeout_s: float = 5.0) -> None:
        state = self.states.get(run_id)
        waited = 0.0
        while state is not None and not state.finished and waited < timeout_s:
            await asyncio.sleep(POLL_S)
            waited += POLL_S

    # --- подписка SSE -----------------------------------------------------------------------------

    async def stream(self, run_id: int, start: int = 0) -> AsyncIterator[ServerSentEvent]:
        state = self.states.get(run_id)
        if state is None:
            return
        index = start
        while True:
            while index < len(state.events):
                name, data = state.events[index]
                yield ServerSentEvent(data=data, event=name, id=str(index))
                index += 1
            if state.finished and index >= len(state.events):
                return
            await asyncio.sleep(POLL_S)

    @staticmethod
    def stored(events: list[Any], start: int = 0) -> list[ServerSentEvent]:
        return [
            ServerSentEvent(data=e.data.model_dump_json(), event=e.event, id=str(i))
            for i, e in enumerate(events)
            if i >= start
        ]

    # --- жизненный цикл ----------------------------------------------------------------------------

    def recover(self) -> int:
        with Session(get_engine()) as session:
            return runs_service.mark_interrupted(session)

    async def shutdown(self) -> None:
        for state in self.states.values():
            if not state.finished:
                state.cancel.set()
        tasks = list(self._tasks)
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        for task in tasks:
            task.cancel()
