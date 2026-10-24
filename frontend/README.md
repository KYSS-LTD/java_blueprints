# Frontend

Next.js (App Router) + TypeScript + shadcn/ui + Tailwind CSS. Подробности — в [docs/09-frontend.md](../docs/09-frontend.md).

```bash
pnpm install
pnpm dev          # http://localhost:3000, /api/* проксируется на BACKEND_URL (по умолчанию http://localhost:8000)
pnpm lint
pnpm typecheck    # next typegen + tsc --noEmit
pnpm test         # vitest
pnpm gen:api      # типы из openapi.json (его выгружает backend: см. корневой README)
pnpm dev:mock     # то же, что dev, но на моках (NEXT_PUBLIC_MOCK=1): backend не нужен
```

## Моки

`NEXT_PUBLIC_MOCK=1` подменяет `fetch` клиента (`src/lib/api/client.ts`) на `src/mocks/mock-fetch.ts`, который отвечает JSON-фикстурами из `src/mocks/fixtures/`; SSE запуска проигрывается из `run-8-events.json` (`src/lib/api/sse.ts`). Без переменной моки в сборку не попадают. Откройте `/preview`: список методов `demo-orders` и блок-схема выбранного метода (блок-схемы есть у `mergeDuplicates`, `distinctCustomers`, `buildCsv`, `findOrderById`).

Фикстуры генерирует `scripts/gen_mock_fixtures.py` из `samples/demo-orders` и проверяет схемами контракта; руками их не правят:

```bash
cd backend && uv run python ../scripts/gen_mock_fixtures.py          # перегенерировать
cd backend && uv run python ../scripts/gen_mock_fixtures.py --check  # сверить с диском (то же делает pytest)
```

Незамоканные эндпоинты в режиме моков отвечают `501 NOT_IMPLEMENTED` в обычном формате ошибки, как и настоящий backend до этапа S3.

## Рабочая область

`/` — проекты и импорт, `/projects/[id]` — рабочая область (дерево, холст блок-схемы, граф структуры, Monaco, консоль, анализ). Параметры адреса `?file=Main.java&method=<id>&node=<id>` воспроизводят позицию.

- Графы: `src/lib/graph/` — модель блок-схемы (`flow-model.ts`), подготовка графа для ELK (`flow-layout.ts`), поиск узла по позиции курсора (`locate.ts`); раскладка считается в Web Worker (`src/workers/elk.worker.ts`), холст — `src/components/graph/`.
- Состояние: TanStack Query для данных сервера, Zustand — `src/stores/workspace-store.ts` (вкладки, выбор узла, свёрнутость контейнеров) и `run-store.ts` (запуски и SSE).
- Monaco лежит в `public/monaco` (в git не хранится): `pnpm dev`, `pnpm dev:mock` и `pnpm build` копируют его из `node_modules` через `scripts/copy-monaco.mjs`; без этого редактор не загрузится.
- Минимальное разрешение — 1280×720; нижние панели идут тремя колонками от 1500 px, на более узком экране — вкладками.

Заметки:

- Компоненты `src/components/ui/` — исходники shadcn/ui (стиль new-york), `components.json` совместим с CLI `shadcn add`.
- Шрифты (Inter, JetBrains Mono) подключены из npm (`@fontsource-variable/*`): сборка не зависит от Google Fonts.
- Все строки интерфейса — в `src/lib/i18n/ru.ts`.
- В `next.config.ts` отключено сжатие (`compress: false`), иначе SSE-события запусков приходят пачкой.
