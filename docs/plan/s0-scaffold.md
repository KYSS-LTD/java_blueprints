# S0. Каркас и окружение

**Часы:** H0–3 · **Треки:** все · **Веха:** вход в M0

## Цель

Дать каждому исполнителю рабочее окружение и пустые, но запускаемые backend и frontend, чтобы S1–S4 шли параллельно без простоя. Проверить главный технический риск: работоспособность tree-sitter на Windows и в Docker.

## Зависимости

Нет. Установлены Python 3.12 + `uv`, Node 22 + `pnpm`, JDK 21, Docker.

## Задачи

| ID | Задача | Трек | Ч | Результат |
|---|---|---|---|---|
| S0.1 | Репозиторий и структура по [03 § 6](../03-tech-stack.md#6-структура-репозитория): каталоги `backend/`, `frontend/`, `samples/`, `reports/`, `docs/`, `scripts/`; `.gitignore`, `.editorconfig`, корневой `README.md` (заготовка: что это, как запустить, раздел «Статус»), `Makefile` (`dev`, `test`, `lint`, `submit`) | все | 0,5 | Структура в `main` |
| S0.2 | Backend-скелет: `pyproject.toml` (FastAPI, uvicorn, pydantic-settings, sqlmodel, tree-sitter, tree-sitter-java, networkx, sse-starlette, python-multipart, pytest, httpx, ruff), `uv.lock`; `app/main.py` с `create_app()`, `config.py` (`JB_*`), единый формат ошибок, структурные логи, `GET /api/health` (версия `javac`, запись в БД) | B | 1 | `uvicorn` стартует, `/api/health` отвечает |
| S0.3 | БД: SQLModel-модели `project`, `version`, `source_file`, `scenario`, `baseline`, `run`, `run_item` ([02 § 5.1](../02-architecture.md#51-sqlite-sqlmodel)); `create_all` при старте, WAL, `foreign_keys=ON`; фабрика сессий | B | 0,5 | Таблицы создаются; тест на CRUD |
| S0.4 | Frontend-скелет: `create-next-app` (App Router, TS, Tailwind v4), `shadcn init` и добавление базовых компонентов (Button, Tabs, ScrollArea, Resizable, Input, Textarea, Badge, Card, Dialog, Sheet, Tooltip, DropdownMenu, ContextMenu, Progress, Table, Select, Collapsible, Alert, Sonner, Command, Skeleton, Chart); тёмная тема по умолчанию; шрифты Inter и JetBrains Mono; `lib/i18n/ru.ts`; `rewrites` `/api/*` → backend; страница `/` показывает результат `/api/health` | C | 1 | `pnpm dev` показывает статус backend |
| S0.5 | Генерация типов: `pnpm gen:api` (`openapi-typescript` из `/api/openapi.json`), `lib/api/client.ts` на `openapi-fetch`, провайдер TanStack Query | C | 0,5 | Типы генерируются, `health` вызывается типизированно |
| S0.6 | Docker: `backend/Dockerfile` (multi-stage, JDK 21, пользователь `runner`, `LANG=C.UTF-8`), `frontend/Dockerfile` (standalone), `docker-compose.yml` с healthcheck и томом `jb-data`; `.dockerignore` | B | 1 | `docker compose up --build` поднимает оба сервиса |
| S0.7 | **Спайк tree-sitter-java:** разбор файла с кириллицей и `record`/`enum`/лямбдой/switch-выражением; получение диапазонов (байты → UTF-16 колонки); обнаружение `ERROR` на сломанном файле; `Query`-API выбранной версии; проверка на Windows (нативно) и в Docker; зафиксировать версии в `uv.lock`; обёртка `engine/java/ts.py` | A | 1,5 | Тест `test_ts_spike.py` зелёный на обеих платформах; в `03-tech-stack.md` зафиксирована рабочая версия |
| S0.8 | CI (GitHub Actions): `backend` (ruff, pytest), `frontend` (eslint, tsc, vitest, build), проверка актуальности `schema.d.ts` | все | 0,5 | Зелёный CI на `main` |
| S0.9 | Скрипт проверки JDK и инструментов `scripts/doctor.py` (python, uv, node, pnpm, `javac -version`, docker); на Python, а не на shell, чтобы работал и на Windows | B | 0,25 | Скрипт выводит чек-лист окружения |

Параллельность: S0.2–S0.3 (B), S0.4–S0.5 (C), S0.7 (A) идут одновременно; S0.6 и S0.8 — по готовности скелетов.

## Артефакты

- Запускаемые backend и frontend; Docker-сборка; CI.
- Результат спайка tree-sitter и рабочая обёртка `ts.py`.

## Критерии выхода

- [ ] `docker compose up --build` с чистого клона открывает `localhost:3000` со статусом backend «ok, javac 21».
- [ ] `uv run pytest` и `pnpm test` зелёные (минимальные тесты есть).
- [ ] CI зелёный.
- [ ] Спайк tree-sitter подтверждает: Java 21 синтаксис разбирается, диапазоны корректны для кириллицы, ошибки обнаруживаются.
- [ ] Если спайк провалился: решение принято (сборка из исходников / разработка в Docker) и записано в [03](../03-tech-stack.md).

## Статус выполнения

| Задача | Состояние | Примечание |
|---|---|---|
| S0.1 Репозиторий и структура | ✅ | `git init`, `.gitignore`, `.editorconfig`, `Makefile` (на Windows без `make` команды из README выполняются напрямую) |
| S0.2 Backend-скелет | ✅ | FastAPI, единый формат ошибок, JSON-логи с `request_id`, `/api/health` (версии JDK, БД); 16 тестов, `ruff` и `mypy` чистые |
| S0.3 БД | ✅ | Семь таблиц SQLModel, WAL, `foreign_keys=ON`; тесты на связи, уникальность и JSON-поля |
| S0.4 Frontend-скелет | ✅ | Next 16 + Tailwind 4 + 30 компонентов shadcn, тёмная тема, `ru.ts`, rewrites, главная показывает `/api/health` через прокси |
| S0.5 Генерация типов | ✅ | `app.export_openapi` → `frontend/openapi.json` → `pnpm gen:api`; `openapi-fetch`, TanStack Query |
| S0.6 Docker | ✅ | образы собраны, `docker compose up` поднимает backend и frontend (JDK 21, пользователь `runner`); найден и исправлен путь к `samples` в контейнере (`JB_SAMPLES_DIR`) |
| S0.7 Спайк tree-sitter | ✅ на Windows и в Docker | 8 тестов зелёные (Java 21, кириллица, эмодзи, CRLF, ошибки, `Query`); на Linux/в Docker проверит CI |
| S0.8 CI | ⚠️ | `.github/workflows/ci.yml` написан (4 задачи), синтаксис YAML проверен, но не запускался: репозиторий ещё не опубликован |
| S0.9 `doctor.py` | ✅ | |

Отклонения от плана (версии, источник компонентов shadcn, шрифты) — в [03 § 8](../03-tech-stack.md#8-фактические-версии-и-отклонения-итоги-s0).
