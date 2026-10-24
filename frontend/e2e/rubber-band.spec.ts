import { expect, test } from "@playwright/test";

import { blocks, createProject, openWorkspace } from "./helpers";

// S5: Shift+перетаскивание по полю холста выделяет блоки рамкой (подряд идущие операторы одного слота).
test("rubber_band: рамка вокруг нескольких блоков выделяет их, панель показывает число", async ({ page, request }) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
  await expect.poll(() => blocks(page).count()).toBeGreaterThan(2);

  const operations = page.locator('[data-testid^="block-operation"]');
  const count = await operations.count();
  expect(count).toBeGreaterThanOrEqual(3);
  const first = (await operations.nth(0).boundingBox())!;
  const last = (await operations.nth(2).boundingBox())!;
  const left = Math.min(first.x, last.x) - 24;
  const top = first.y - 36;
  const right = Math.max(first.x + first.width, last.x + last.width) + 24;
  const bottom = last.y + last.height + 30;

  await page.keyboard.down("Shift");
  await page.mouse.move(left, top);
  await page.mouse.down();
  await page.mouse.move((left + right) / 2, (top + bottom) / 2, { steps: 6 });
  await page.mouse.move(right, bottom, { steps: 6 });
  await page.mouse.up();
  await page.keyboard.up("Shift");

  await expect(page.getByTestId("selection-bar")).toBeVisible();
  const text = await page.getByTestId("selection-count").innerText();
  expect(Number(text.split(":")[1].trim())).toBeGreaterThanOrEqual(3);
});
