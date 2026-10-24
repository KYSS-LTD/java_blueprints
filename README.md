# Java Blueprints

Визуальная среда программирования для Java: программа показывается одновременно как код и как граф (блок-схема). Инструмент находит дефекты и оценивает сложность по Big O, помогает оптимизировать код и разбить монолит на классы, а после каждого преобразования доказывает автотестами, что поведение сохранилось.

Кейс AXENIX, 48-часовой хакатон. Документация и план разработки — в [docs/](docs/README.md).

**Стек:** Python 3.12 + FastAPI + tree-sitter-java (backend), Next.js + shadcn/ui + React Flow + Monaco (frontend), SQLite, JDK 21 для сборки и запуска Java.

## Быстрый старт

### Docker (рекомендуется)

```bash
docker compose up --build
```

Приложение: <http://localhost:3000>. Наружу открыт только frontend, он проксирует `/api/*` в backend.

### Локально (для разработки)

Нужны Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22+, pnpm и JDK 21+ (`javac` в `PATH`). Проверить окружение:

```bash
python scripts/doctor.py
```

Backend (порт 8000):

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Frontend (порт 3000, `/api` проксируется на `localhost:8000`):

```bash
cd frontend
pnpm install
pnpm dev
```

Проверка связки: <http://localhost:3000> показывает состояние сервиса (версия, JDK, БД); то же — `GET http://localhost:8000/api/health`.

### Тесты и проверки

```bash
cd backend  && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
cd frontend && pnpm lint && pnpm typecheck && pnpm test
```

Корпус примеров и моки:

```bash
python scripts/verify_corpus.py                          # javac + два прогона каждого сценария (нужен JDK)
cd frontend && pnpm dev:mock                             # frontend без backend, /preview на моках
cd backend  && uv run python ../scripts/gen_mock_fixtures.py   # перегенерировать моки после правки схем или demo-orders
```

### Контракт API

Схемы API описаны в Pydantic; TypeScript-типы для frontend генерируются из OpenAPI. После изменения схем:

```bash
cd backend  && uv run python -m app.export_openapi ../frontend/openapi.json
cd frontend && pnpm gen:api
```

Обновлённые `frontend/openapi.json` и `frontend/src/lib/api/schema.d.ts` коммитятся; CI проверяет, что они актуальны.

## Структура

```
backend/    FastAPI (app/) и ядро без веб-зависимостей (engine/): разбор Java, граф, правка, анализ, запуск, проверка
frontend/   Next.js, shadcn/ui, граф и редактор
samples/    корпус Java-примеров: демо-монолит, фикстуры scope, сломанные и вне рамок (см. samples/README.md)
reports/    результаты проверки поведения для сдачи
scripts/    служебные скрипты: doctor.py, verify_corpus.py, verify_samples.py, bug_bash.py, perf_budget.py, submit.py, gen_mock_fixtures.py (моки frontend)
docs/       документация и план разработки
```

## Демо-путь

С `docker compose up --build` (или локально с `JB_SEED_DEMO=1`) на старте создаётся проект «Demo: заказы» — монолит на ≈1000 строк с 11 сценариями; в фоне записывается эталон и проходит gate, поэтому в шапке сразу «✓ поведение совпадает (11/11)». Для демо «с нуля» импортируйте `samples/demo-orders/Main.java` кнопкой «Импорт Java» (или запустите без `JB_SEED_DEMO`). Пошаговый сценарий на 7 минут — [docs/12-demo-and-submission.md](docs/12-demo-and-submission.md).

## Проверка и отчёты

```bash
python scripts/verify_samples.py   # 6 примеров целиком через API → reports/verify_samples.md|json
python scripts/bug_bash.py         # весь корпус (48 проектов): граф всех методов, исправления, javac на экспорте → reports/bug_bash.md
python scripts/perf_budget.py      # бюджеты docs/10 § 3.6 → reports/perf_budget.md
make submit                        # тесты + отчёты + архив dist/java-blueprints-submission.zip (reports/SUMMARY.md)
```

| Бюджет (`demo-orders`, 995 строк) | Замер |
|---|---|
| Разбор + граф всех методов (≤ 0,3 с) | 0,04 с |
| Операция над графом (≤ 0,5 с) | 0,05 с |
| Анализ (≤ 1 с) | 0,41 с |
| Холодная компиляция (≤ 3 с) | 1,5 с |
| Gate на 15 сценариях (≤ 20 с) | 3,3 с |

## Статус

Все этапы плана ([docs/plan](docs/plan/README.md)) реализованы; подробная приёмка по пунктам ТЗ и список ограничений — [docs/13-acceptance.md](docs/13-acceptance.md), описание решения — [SOLUTION.md](SOLUTION.md).

| Этап | Состояние |
|---|---|
| S0–S3 Каркас, контракт API, разбор и граф, backend-ядро | сделаны |
| S4–S5 Рабочая область и двусторонняя правка граф ↔ код | сделаны (undo/redo, история, палитра, инспектор, мультивыбор) |
| S6 Проверка поведения | сделан: сценарии, эталон, gate, трассировка, генератор, бенчмарк |
| S7 Диагностика | сделан: 33 правила, Big O, метрики, «Проверить» |
| S8 Оптимизация | сделан: 21 автоисправление, пакетная «Оптимизировать» с измеренным ускорением (нет `OPT-10`) |
| S9 Выделение метода и декомпозиция | сделаны |
| S10 Приёмка и сдача | отчёт приёмки, баг-баш, бюджеты, `SOLUTION.md`, `make submit`; не сделаны Playwright E2E, видео и проверка Docker на чистой машине |

Проверено на Windows и JDK 17; JDK 21 (целевой), Linux и Docker проверяет CI и ручной прогон на чистой машине.
