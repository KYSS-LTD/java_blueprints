# 08. API

REST + SSE. Префикс `/api`. Формат — JSON (UTF-8). Схемы — Pydantic v2; OpenAPI доступен на `/api/openapi.json`, из него генерируются типы frontend (`pnpm gen:api`).

## 1. Соглашения

- Параметры пути названы по сущности: `{project_id}`, `{version_id}`, `{scenario_id}`, `{run_id}` (в таблицах ниже — `{id}`/`{vid}`/`{sid}`/`{rid}`). Составные идентификаторы (`{path}` файла, `{node_id}`, `{finding_id}`) занимают остаток пути и могут содержать `/`; клиент кодирует их `encodeURIComponent`. Параметр `from` у `/diff` в Python называется `from_version`.
- Идентификаторы проектов, версий, сценариев, запусков — целые числа. ID узлов графа — строки (см. [04 § 5.3](04-code-graph-model.md#53-идентификаторы-узлов)).
- Все диапазоны кода: `{file, start_line, start_col, end_line, end_col}` (строки с 1, колонки в UTF-16 с 1).
- Время — ISO 8601 UTC. Длительности — миллисекунды.
- Параметр `version_id` в запросах чтения необязателен: по умолчанию — HEAD проекта.
- Ошибки:

```json
{"error": {"code": "INVALID_FRAGMENT", "message": "Неверное выражение в условии", "details": {"field": "condition", "line": 1, "column": 8}}}
```

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `BAD_REQUEST` | Неверные параметры запроса |
| 404 | `NOT_FOUND` | Проект/версия/файл/узел не найден |
| 409 | `VERSION_CONFLICT` | `base_version_id` ≠ HEAD |
| 413 | `PAYLOAD_TOO_LARGE` | Файл > 2 МБ или архив > 10 МБ |
| 422 | `INVALID_FRAGMENT` | Фрагмент не парсится или нарушает области видимости |
| 422 | `PRECONDITION_FAILED` | Не выполнены предусловия преобразования |
| 422 | `SYNTAX_ERROR` | Операция над файлом с синтаксическими ошибками |
| 422 | `UNSUPPORTED` | Конструкция вне scope для этой операции |
| 429 | `BUSY` | Слишком много параллельных запусков |
| 500 | `INTERNAL` | Неожиданная ошибка |
| 503 | `JDK_UNAVAILABLE` | `javac`/`java` не найдены на сервере (`/api/health` показывает причину) |
| 501 | `NOT_IMPLEMENTED` | Эндпоинт описан контрактом, но ещё не реализован (в сообщении — этап плана); только до этапа S10 |

В OpenAPI любая ошибка описана одним ответом `default` с моделью `ErrorResponse`; ошибки валидации запроса возвращаются как `400 BAD_REQUEST`, а не стандартный `422` FastAPI.

## 2. Проекты и импорт

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/health` | Состояние: версия приложения, `javac` (версия JDK), БД |
| `GET` | `/api/samples` | Список встроенных примеров `[{id, name, description, lines}]` |
| `POST` | `/api/projects` | Создать проект (см. ниже) |
| `GET` | `/api/projects` | Список проектов |
| `GET` | `/api/projects/{id}` | Проект: имя, HEAD, оригинал, эталонная версия, main-класс, настройки, статус gate HEAD |
| `PATCH` | `/api/projects/{id}` | Имя, main-класс, настройки (таймауты, пороги правил, локаль) |
| `DELETE` | `/api/projects/{id}` | Удалить проект |

`POST /api/projects` принимает один из вариантов:

- `multipart/form-data`: поля `name`, `files[]` (`.java`) **или** `archive` (`.zip`);
- `application/json`: `{"name": "...", "files": [{"path": "Main.java", "content": "..."}]}` — вставка текста;
- `application/json`: `{"name": "...", "sample_id": "demo-orders"}` — встроенный пример.

Защита: пути нормализуются, `..` и абсолютные пути отклоняются (zip-slip); принимаются только `.java`; кодировка UTF-8 (BOM удаляется; не UTF-8 → `400`).

Ответ `201`:

```json
{
  "id": 1, "name": "demo-orders", "head_version_id": 1, "original_version_id": 1,
  "main_class": "Main", "main_class_candidates": ["Main"],
  "syntax_errors": [], "files": [{"path": "Main.java", "lines": 1012}]
}
```

## 3. Версии и файлы

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/projects/{id}/versions` | История: `[{id, number, parent_id, origin, description, created_at, gate_status, status}]` |
| `GET` | `/api/projects/{id}/versions/{vid}` | Версия + список файлов |
| `POST` | `/api/projects/{id}/versions/{vid}/restore` | Сделать версию HEAD (создаётся версия `revert`) |
| `POST` | `/api/projects/{id}/undo` | HEAD → родитель |
| `POST` | `/api/projects/{id}/redo` | HEAD → потомок (если есть) |
| `GET` | `/api/projects/{id}/files` | Дерево файлов версии (`?version_id`) |
| `GET` | `/api/projects/{id}/files/{path}` | Содержимое файла (`?version_id`) |
| `PUT` | `/api/projects/{id}/files/{path}` | Сохранить файл: `{base_version_id, content}` → новая версия `code_edit` (схлопывание подряд идущих правок) |
| `POST` | `/api/projects/{id}/files` | Создать файл (`{path, content}`) |
| `DELETE` | `/api/projects/{id}/files/{path}` | Удалить файл (новая версия) |
| `GET` | `/api/projects/{id}/diff` | `?from=<vid>&to=<vid>` → по файлам: unified diff и текст сторон |
| `POST` | `/api/projects/{id}/parse` | Живой разбор без сохранения: `{file, content, method_key?}` → `{syntax_errors[], flow?}` |

## 4. Граф

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/projects/{id}/structure` | `StructureGraph` (`?version_id`, `?mode=types|methods|deps`) |
| `GET` | `/api/projects/{id}/flow` | `FlowGraph` метода (`?method_id=…`, `?version_id`) |
| `GET` | `/api/projects/{id}/tree` | Дерево «Проект»: пакеты → файлы → типы → члены, с бейджами находок |
| `GET` | `/api/projects/{id}/nodes/{node_id}` | Подробности узла: поля (`data`), диапазон, оценка сложности, находки |
| `GET` | `/api/projects/{id}/locate` | `?file&line&column` → `{node_id, method_id}` (синхронизация курсора с графом) |

`node_id` в путях кодируется (`encodeURIComponent`); для длинных ID доступен вариант `POST /nodes/lookup` с телом `{node_id}`.

## 5. Операции над графом

`POST /api/projects/{id}/ops`

```json
{
  "base_version_id": 12,
  "ops": [
    {"op": "insert_node", "parent_id": "Main.java#Main.processOrders(List<Order>)", "slot": "body", "index": 2,
     "kind": "condition", "fields": {"expr": "orders == null", "add_else": false}},
    {"op": "update_node", "node_id": "…/for[0]/if[0]", "fields": {"expr": "validator.isValid(order)"}}
  ],
  "description": "Добавить проверку на null"
}
```

Ответ `200`:

```json
{
  "version": {"id": 13, "number": 13, "origin": "graph_op"},
  "changed_files": ["Main.java"],
  "focus_node_id": "Main.java#Main.processOrders(List<Order>)/if[0]",
  "id_map": {"…/if[0]": "…/if[1]"},
  "flow": { "…": "FlowGraph изменённого метода" },
  "syntax_errors": []
}
```

Перечень операций — [04 § 5.2](04-code-graph-model.md#52-граф--код-операции). Атомарность: при ошибке любой операции ни одна не применяется.

Дополнительно:

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/projects/{id}/ops/validate` | Сухая проверка операций без сохранения: `{ok, errors[], diff}` (для предпросмотра и подсветки ошибок в форме) |
| `GET` | `/api/projects/{id}/palette` | Описание блоков палитры и их полей (`kind`, `fields[]` с типами и валидаторами) |
| `POST` | `/api/projects/{id}/validate/expression` | `{kind: expression|type|identifier, text, context_node_id}` → `{ok, error?}` |

## 6. Анализ

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/projects/{id}/analysis` | Полный результат для версии: `{findings[], summary, complexity: {current, optimized}, metrics}` |
| `GET` | `/api/projects/{id}/findings` | Находки с фильтрами (`severity`, `category`, `file`, `fixable`) |
| `GET` | `/api/projects/{id}/findings/{fid}` | Карточка: объяснение, вклады, доказательства, исправление |
| `GET` | `/api/projects/{id}/complexity` | `?node_id=…` или `?file&start_line&end_line` → оценка узла/фрагмента с цепочкой вкладов |
| `GET` | `/api/projects/{id}/metrics` | Метрики версии (`?compare_to=<vid>` для «до/после») |
| `POST` | `/api/projects/{id}/findings/{fid}/verify` | Динамическая проверка находки (запуск `kind=benchmark`/`trace`) → `run_id` |

Структура `Finding`:

```json
{
  "id": "PERF-01:Main.java#…/for[0]",
  "rule_id": "PERF-01",
  "severity": "high",
  "category": "PERF",
  "title": "Вложенный цикл — O(n²)",
  "location": {"file": "Main.java", "start_line": 42, "end_line": 48, "node_id": "…", "method_id": "…"},
  "explanation": "Внешний и внутренний циклы обходят orders …",
  "defect": "Повторная обработка одинаковых пар",
  "risk": "Рост времени при увеличении входа",
  "recommendation": "Индексировать элементы по id",
  "complexity": {"current": "O(n²)", "after": "O(n)", "confidence": "exact",
                 "contributions": [{"node_id": "…", "factor": "n", "why": "…"}]},
  "fix": {"fix_id": "OPT-01", "available": true, "behavior_preserving": true,
          "preconditions": [{"name": "Тело зависит только от пары", "passed": true, "reason": null}]},
  "evidence": {"static": [...], "dynamic": null}
}
```

## 7. Преобразования

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/projects/{id}/fixes` | Доступные исправления для версии |
| `POST` | `/api/projects/{id}/fixes/{finding_id}/preview` | Предпросмотр: diff, эффект, предусловия |
| `POST` | `/api/projects/{id}/fixes/{finding_id}/apply` | `{base_version_id, params?}` → создаётся кандидат и запускается gate: `{candidate_version_id, run_id}` |
| `POST` | `/api/projects/{id}/optimize/plan` | План пакетной оптимизации: шаги, эффект |
| `POST` | `/api/projects/{id}/optimize/apply` | Применить выбранные шаги последовательно: `{base_version_id, step_ids[]}` → `run_id` (SSE с прогрессом шагов) |
| `POST` | `/api/projects/{id}/extract-method/preview` | `{node_ids[] \| range, name?}` → код и diff |
| `POST` | `/api/projects/{id}/extract-method/apply` | → кандидат + gate (как у `fixes/apply`) |
| `POST` | `/api/projects/{id}/decompose/plan` | `{options?}` → `DecomposePlan` |
| `POST` | `/api/projects/{id}/decompose/rescore` | `{base_version_id, plan}` → `DecomposePlan`: после ручной правки пересчитывает строки классов, связи, предупреждения и метрики «после». Состав и имена берутся из запроса, код не генерируется и не компилируется (быстрый вызов для перетаскивания методов). Недопустимый план (занятое имя, перенос закреплённого члена, класс без методов) — `422` с причиной, как при предпросмотре |
| `POST` | `/api/projects/{id}/decompose/preview` | `{base_version_id, plan}` → пересчитанный `plan`, diff по файлам (новые — `added`) и предупреждения, в том числе «результат не компилируется» |
| `POST` | `/api/projects/{id}/decompose/apply` | `{base_version_id, plan}` → кандидат + gate |
| `POST` | `/api/projects/{id}/versions/{vid}/accept` | Подтвердить кандидата после красного gate (для `behavior_preserving = false`): обновляет эталоны затронутых сценариев |
| `POST` | `/api/projects/{id}/versions/{vid}/reject` | Отклонить кандидата |

`DecomposePlan`:

```json
{
  "classes": [
    {"name": "Parser", "role": "parser", "file": "Parser.java", "members": ["…method ids"], "lines": 210, "reason": "методы parse*, read*"},
    {"name": "Analyzer", "role": "analyzer", "members": ["…"], "lines": 300},
    {"name": "Main", "role": "core", "members": ["main", "…"], "lines": 120}
  ],
  "edges": [{"from": "Main", "to": "Parser", "calls": 4, "fields": 0}],
  "warnings": ["Метод summarize использует 5 полей другого класса"],
  "metrics": {"before": {"lines": 1012, "lcom4": 5}, "after": {"lines": 640, "lcom4": 1.6}},
  "pinned": {"Main.java#Main.main(String[])": "точка входа", "Main.java#Main.lines": "состояние остаётся в ядре"}
}
```

`pinned` — члены, которые остаются в ядре, и причина (поля, конструкторы, `main`, методы с внутренними классами, `this` в анонимных классах, унаследованными членами, ссылками `Тип::метод`). Их нельзя переносить: интерфейс запирает такие члены, а `rescore`, `preview` и `apply` отвечают `422`. Классы с `role=model` — типы, вынесенные в свои файлы целиком (в `members` их id): методы в них не переносятся.

## 8. Сценарии, запуски, проверка

### 8.1 Сценарии

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/projects/{id}/scenarios` | Список со статусом эталона и стабильностью |
| `POST` | `/api/projects/{id}/scenarios` | Создать: `{name, description?, stdin, args[]}` |
| `PUT` | `/api/projects/{id}/scenarios/{sid}` | Изменить (сбрасывает эталон) |
| `DELETE` | `/api/projects/{id}/scenarios/{sid}` | Удалить |
| `POST` | `/api/projects/{id}/scenarios/import` | Импорт `.json` / набор `*.in`+`*.out` |
| `GET` | `/api/projects/{id}/scenarios/export` | Экспорт всех в JSON |
| `POST` | `/api/projects/{id}/scenarios/generate` | Генерация → `run_id` (`kind=generate`); результат — сценарии с `source=generated` |
| `GET` | `/api/projects/{id}/input-schema` | Выведенная схема ввода (для отладки/отображения) |

### 8.2 Запуски

`POST /api/projects/{id}/runs`

```json
{"kind": "test", "version_id": 13, "scenario_ids": null}
```

| `kind` | Что делает |
|---|---|
| `run` | Компиляция + запуск с произвольным `stdin`/`args` (поля тела); одно выполнение |
| `trace` | То же, на инструментированной сборке; вернёт `hits` по узлам (кнопка «Дебаг») |
| `baseline` | Записать эталон на версии для выбранных сценариев |
| `test` | Сравнить версию с эталоном по всем включённым сценариям |
| `gate` | `syntax → compile → scenarios (+ coverage)`; результат присваивается версии |
| `generate` | Генерация сценариев |
| `benchmark` | Масштабируемые сценарии: до/после, график роста |
| `verify` | Динамическая проверка находки |

Ответ `202`: `{"run_id": 7, "status": "queued"}`.

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/runs/{rid}` | Статус и итог: `{status, kind, version_id, summary, items[]}` |
| `GET` | `/api/runs/{rid}/events` | SSE-поток (раздел 9) |
| `POST` | `/api/runs/{rid}/cancel` | Отмена |
| `GET` | `/api/projects/{id}/runs` | История запусков |
| `GET` | `/api/runs/{rid}/report` | `?format=json|md|junit` — отчёт для скачивания |

### 8.3 Сборка

`POST /api/projects/{id}/build` → синхронная компиляция HEAD (или `version_id`): `{ok, diagnostics[], duration_ms}`; кеш по хешу. Нужен для быстрых ошибок компиляции в маркерах Monaco.

## 9. SSE-события

`GET /api/runs/{rid}/events` (`Content-Type: text/event-stream`). Поздние подписчики получают replay с начала; с заголовком `Last-Event-ID: N` (его шлёт `EventSource` при переподключении) поток продолжается после события `N`; `id` события — его порядковый номер с 0. Если запуска нет в памяти сервера (перезапуск), события восстанавливаются из БД: `status`, `item`, `result`, `status`, `done`. Формат `event:` + `data:` (JSON).

| Событие | Данные |
|---|---|
| `status` | `{status: queued|running|done|failed|cancelled}` |
| `log` | `{stream: "build|stdout|stderr|info", text}` |
| `gate` | `{stage: syntax|compile|scenarios|coverage, status: running|passed|failed, details?}` |
| `item` | `{scenario_id, name, verdict: PASS|FAIL|ERROR|TIMEOUT, duration_ms, baseline_ms, diff?}` |
| `step` | Для `optimize`: `{step_id, title, status: applying|passed|skipped, reason?}` |
| `progress` | `{done, total}` |
| `result` | Итог: `{passed, total, coverage, version_status}` или данные трассировки/бенчмарка |
| `error` | `{code, message}` |
| `done` | Завершение потока |

Heartbeat-комментарий каждые 15 секунд. Клиент (`EventSource` с повтором) заново подписывается и получает replay.

## 10. Экспорт

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/projects/{id}/export/source` | `?version_id&format=zip|file&path=` — исходники версии (zip: `src/…`) |
| `GET` | `/api/projects/{id}/export/report` | Сводный отчёт: анализ + преобразования + результаты тестов (`format=md|json`) |
| `GET` | `/api/projects/{id}/export/bundle` | Zip: исходники версии + отчёты + сценарии (для комплекта сдачи) |

## 11. Лимиты и безопасность

- Размер файла ≤ 2 МБ, архива ≤ 10 МБ, число файлов ≤ 200.
- Одновременно активных JVM — `JB_MAX_PARALLEL_RUNS`; остальные ждут в очереди.
- Нет аутентификации (вне рамок кейса); API слушает внутреннюю сеть compose, наружу открыт только frontend-прокси.
- Имена файлов проверяются; в рабочие каталоги пишется только содержимое проекта; пути вычисляются от корня каталога сборки.

## 12. Версионирование API

Один набор эндпоинтов без префикса версии (хакатон). Любое изменение схемы сопровождается перегенерацией `frontend/src/lib/api/schema.d.ts`; CI проверяет, что файл актуален (`pnpm gen:api && git diff --exit-code`).
