# 11. Запуск и развёртывание

## 1. Требования

| Способ | Нужно |
|---|---|
| Docker (рекомендуется, воспроизводимая сборка для сдачи) | Docker 24+ и Docker Compose v2 |
| Локально (разработка) | Python 3.12 + `uv`, Node.js 22 + `pnpm`, **JDK 21** (`javac` и `java` в `PATH`) |

## 2. Запуск через Docker Compose

```bash
docker compose up --build
```

Приложение: <http://localhost:3000>. Backend доступен только внутри сети compose (frontend проксирует `/api/*`); для отладки порт 8000 можно опубликовать через `docker-compose.override.yml`.

### 2.1 `docker-compose.yml` (структура)

Полный файл — [docker-compose.yml](../docker-compose.yml) в корне. Контекст сборки обоих образов — **корень репозитория** (в образ backend копируется `samples/`), Dockerfile лежат в `backend/` и `frontend/`.

```yaml
services:
  backend:
    build: { context: ., dockerfile: backend/Dockerfile }
    environment:
      JB_DATA_DIR: /data
      JB_MAX_PARALLEL_RUNS: "4"
      JB_RUN_TIMEOUT_S: "10"
    volumes:
      - jb-data:/data
    healthcheck:   # curl в slim-образе нет, проверяем средствами Python
      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)"]
  frontend:
    build:
      context: .
      dockerfile: frontend/Dockerfile
      args:
        BACKEND_URL: http://backend:8000   # нужен при СБОРКЕ: rewrites Next.js вычисляются в next build
    ports:
      - "3000:3000"
    depends_on:
      backend:
        condition: service_healthy
volumes:
  jb-data:
```

### 2.2 Образ backend

Многостадийная сборка:

1. Стадия `deps` (`python:3.12-slim` + `uv`): `uv sync --frozen --no-dev`.
2. Финальный образ на базе `eclipse-temurin:21-jdk` с Python 3.12 (или `python:3.12-slim` + копирование JDK из `eclipse-temurin:21-jdk`):
   - установленные зависимости, код `app/` и `engine/`, `engine/runtime/java/JbTrace.java` (компилируется при сборке образа), каталог `samples/`;
   - пользователь `runner` (UID 10001) без прав на запись вне `/data/tmp`, `/data/builds`;
   - `ENV LANG=C.UTF-8 PYTHONUTF8=1`;
   - `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`.

Ограничения контейнера (рекомендуется в compose): `mem_limit: 2g`, `cpus: "2"`, `pids_limit: 512`, `read_only: true` с `tmpfs` для `/data/tmp`.

### 2.3 Образ frontend

Многостадийная сборка (`node:22-alpine`): `pnpm install --frozen-lockfile` → `pnpm build` (`output: "standalone"`) → минимальный образ с `node server.js`. `next.config.ts`:

```ts
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig = {
  output: "standalone",
  compress: false,   // сжатие буферизует SSE
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};
```

**`BACKEND_URL` читается при сборке:** Next.js вычисляет `rewrites` в `next build` и записывает результат в манифест. Поэтому в Docker адрес backend задаётся build-аргументом (`http://backend:8000` — имя сервиса compose), а не переменной окружения контейнера. Для другого адреса образ frontend нужно пересобрать.

Для SSE: `compress: false` отключает сжатие, из-за которого события пришли бы пачкой; если впереди стоит nginx, нужен ещё заголовок `X-Accel-Buffering: no`. Потоковую отдачу через rewrite проверяем в S3.6 (первый SSE-эндпоинт).

## 3. Локальная разработка

```bash
# Backend
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# Frontend (в другом терминале)
cd frontend
pnpm install
pnpm dev              # http://localhost:3000, /api проксируется на :8000
```

После изменения схем API типы frontend обновляются так (файл `openapi.json` выгружает backend без запуска сервера):

```bash
cd backend  && uv run python -m app.export_openapi ../frontend/openapi.json
cd frontend && pnpm gen:api
```

Проверка окружения: `python scripts/doctor.py` (Python, uv, Node, pnpm, JDK, git, Docker) и `GET http://localhost:8000/api/health` →
`{"status": "ok", "version": "0.1.0", "javac": "21.0.4", "java": "21.0.4", "db": "ok", "problems": []}`.
Если JDK старше 21 или не найден, `status` будет `degraded`, а причина — в `problems`.

Тесты:

```bash
cd backend  && uv run pytest                      # все
cd backend  && uv run pytest -m "not jdk and not slow"
cd frontend && pnpm test && pnpm e2e
```

## 4. Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `JB_DATA_DIR` | `./data` | БД, сборки, временные каталоги |
| `JB_DB_URL` | `sqlite:///{DATA_DIR}/app.db` | Строка подключения |
| `JB_JAVA_HOME` | из `PATH` | Каталог JDK |
| `JB_MAX_PARALLEL_RUNS` | число CPU (≤ 4) | Параллельные JVM |
| `JB_RUN_TIMEOUT_S` | `10` | Таймаут одного запуска |
| `JB_COMPILE_TIMEOUT_S` | `30` | Таймаут компиляции |
| `JB_OUTPUT_LIMIT_BYTES` | `1048576` | Лимит stdout/stderr |
| `JB_JVM_XMX` | `256m` | Память JVM сценария |
| `JB_KEEP_RUNS` | `0` | Сохранять рабочие каталоги запусков |
| `JB_LOG_LEVEL` | `INFO` | Уровень логов |
| `JB_CORS_ORIGINS` | пусто | Для dev без прокси |
| `BACKEND_URL` (frontend) | `http://localhost:8000` | Цель прокси `/api`; **читается при `next build`** (в Docker — build-аргумент) |

Секретов в проекте нет (LLM не используется).

## 5. Windows-заметки (разработка на Windows)

- JDK 21 в `PATH`; `JAVA_HOME` задан.
- Запуск подпроцессов: `asyncio.create_subprocess_exec`; на Windows требуется `ProactorEventLoop` (по умолчанию в Python 3.12) — проверяется `/health`.
- Остановка по таймауту: `taskkill /PID <pid> /T /F` вместо группы процессов; `setrlimit` недоступен — лимиты памяти обеспечивает `-Xmx`, лимит вывода — чтением потока.
- Пути — `pathlib`; в `javac` передаётся `@sources.txt` (лимит длины команды).
- Для проверки «как в продакшене» использовать Docker (Linux-контейнер).

## 6. Веб-демо

Минимальный вариант сдачи — **воспроизводимая сборка**: `docker compose up`. Опциональный вариант — VPS:

1. Linux-сервер (2 vCPU, 4 ГБ), Docker, домен/IP.
2. `git clone` + `docker compose up -d --build`.
3. Reverse proxy (Caddy/nginx) на порт 3000 с HTTPS; для SSE — отключить буферизацию.
4. Предзагруженный пример: при первом старте backend создаёт проект «Demo: заказы» из `samples/demo-orders` (флаг `JB_SEED_DEMO=1`, в `docker-compose.yml` включён) и в фоне записывает эталон и прогоняет gate (`JB_SEED_PREPARE=1` по умолчанию; нужен JDK). Для демо с нуля отключите флаг и очистите том: `docker compose down -v`.
5. Резервная копия: том `jb-data`.

Так как API не защищён аутентификацией, а пользовательский Java-код выполняется на сервере, публичный стенд закрывается базовой авторизацией на reverse proxy или ограничением по IP (допущение: монолиты доверенные).

## 7. Сборка комплекта сдачи

| Артефакт | Команда / место |
|---|---|
| Исходный код + инструкция | репозиторий, корневой `README.md` |
| Веб-демо | `docker compose up` и (если есть) URL в README |
| Проверка поведения | `scripts/verify_samples.py` → `reports/behavior-report.{md,json,xml}`; `reports/pytest-report.xml` |
| Описание решения | `SOLUTION.md` (архитектура, scope, ограничения) — собирается из `docs/02`, `docs/04 § 9`, `docs/10 § 8` |

`make submit` (или `scripts/make_submission.sh`): собирает образы, запускает тесты, формирует `reports/`, проверяет наличие всех артефактов.
