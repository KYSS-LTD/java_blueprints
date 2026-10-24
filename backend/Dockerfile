# syntax=docker/dockerfile:1
# Контекст сборки — КОРЕНЬ репозитория (нужен каталог samples/): см. docker-compose.yml.

# --- Стадия 1: зависимости Python (uv, по lock-файлу) ---
FROM python:3.12-slim AS deps
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.13.5
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# --- Стадия 2: итоговый образ: Python + JDK 21 ---
FROM python:3.12-slim
# JDK нужен для javac/java: сборка, запуск, gate и трассировка пользовательского кода
COPY --from=eclipse-temurin:21-jdk /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/venv/bin:/opt/java/openjdk/bin:$PATH \
    LANG=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    JB_DATA_DIR=/data \
    JB_SAMPLES_DIR=/app/samples

# Непривилегированный пользователь: от него работают и сервер, и запускаемая Java-программа
RUN useradd --create-home --uid 10001 runner \
    && mkdir -p /data/builds /data/tmp \
    && chown -R runner:runner /data

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY backend/app ./app
COPY backend/engine ./engine
COPY samples ./samples

USER runner
EXPOSE 8000
# Один worker: реестр фоновых задач живёт в памяти процесса (docs/02-architecture.md § 6)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
