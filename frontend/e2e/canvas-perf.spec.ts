import { expect, test } from "@playwright/test";

import { blocks, openWorkspace } from "./helpers";

// docs/10 § 3.6: холст со 150 блоками остаётся интерактивным (INP < 200 мс), раскладка не блокирует страницу.
const BLOCKS_TARGET = 160;
const INP_BUDGET_MS = 200;

/** Метод ~160 блоков: переменные, циклы с ветвлением и вывод — как в длинных методах монолитов. */
function bigMethod(): string {
  const lines: string[] = ["    static int big(int n) {"];
  for (let i = 0; i < 30; i++) lines.push(`        int v${i} = n + ${i};`);
  for (let loop = 0; loop < 6; loop++) {
    lines.push(`        for (int i${loop} = 0; i${loop} < n; i${loop}++) {`);
    lines.push(`            if (v${loop} > i${loop}) {`);
    lines.push(`                v${loop + 1} += i${loop};`);
    lines.push("            } else {");
    lines.push(`                v${loop + 2} -= i${loop};`);
    lines.push("            }");
    lines.push("        }");
  }
  for (let i = 0; i < 110; i++)
    lines.push(`        System.out.println("step ${i}: " + v${i % 30});`);
  lines.push("        return v0 + v1;", "    }");
  return lines.join("\n");
}

const SOURCE = `public class Main {
${bigMethod()}

    public static void main(String[] args) {
        System.out.println(big(3));
    }
}
`;

test("canvas_perf: метод из ~160 блоков раскладывается и откликается быстрее 200 мс", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: { name: "canvas-perf", files: [{ path: "Main.java", content: SOURCE }] },
  });
  expect(created.status()).toBe(201);
  const project = (await created.json()) as { id: number };

  await page.addInitScript(() => {
    const w = window as unknown as { __events: number[] };
    w.__events = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const timing = entry as PerformanceEventTiming & { interactionId?: number };
        if (timing.interactionId) w.__events.push(timing.duration);
      }
    }).observe({ type: "event", durationThreshold: 16, buffered: true } as PerformanceObserverInit);
  });

  const flow = await request.get(`/api/projects/${project.id}/flow`, {
    params: { method_id: "Main.java#Main.big(int)" },
  });
  const nodeCount = ((await flow.json()) as { nodes: unknown[] }).nodes.length;
  expect(nodeCount).toBeGreaterThanOrEqual(BLOCKS_TARGET);

  // при > 120 узлов React Flow рисует только видимые блоки (onlyRenderVisibleElements)
  const started = Date.now();
  await openWorkspace(page, project.id, "Main.java#Main.big(int)");
  // крупная схема открывается на начале метода в читаемом масштабе; худший случай для отрисовки — вся схема сразу
  await expect(page.getByTestId("partial-view")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Вписать в окно" }).click();
  await expect.poll(() => blocks(page).count(), { timeout: 60_000 }).toBeGreaterThan(20);
  const layoutMs = Date.now() - started;

  // взаимодействия: клики по блокам, прокрутка колесом (масштаб) и перетаскивание холста
  const canvas = page.getByTestId("flow-canvas");
  const box = (await canvas.boundingBox())!;
  const items = blocks(page);
  const visible = await items.count();
  for (let i = 0; i < 12; i++) {
    await items.nth((i * 3) % visible).click({ position: { x: 6, y: 6 }, force: true });
  }
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < 6; i++) await page.mouse.wheel(0, i % 2 ? -240 : 240);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 160, box.y + box.height / 2 + 120, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(500);

  const durations = await page.evaluate(
    () => (window as unknown as { __events: number[] }).__events,
  );
  const worst = durations.length ? Math.max(...durations) : 0;
  const drawn = await blocks(page).count();
  console.log(
    `canvas_perf: узлов в схеме ${nodeCount}, нарисовано ${drawn}, раскладка ${layoutMs} мс, событий ${durations.length}, худшее ${worst} мс`,
  );
  expect(worst).toBeLessThan(INP_BUDGET_MS);
});
