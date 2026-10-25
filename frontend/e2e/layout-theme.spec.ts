import { expect, test, type Page } from "@playwright/test";

import { blocks, createProject, openWorkspace } from "./helpers";

// Соответствие макету (слайд 4): нижние панели рядом, переключатель темы, читаемый масштаб крупных схем.
test.use({ viewport: { width: 1440, height: 900 } });

async function zoomPercent(page: Page): Promise<number> {
  const label = await page.getByRole("button", { name: /^Масштаб \d+%/ }).innerText();
  return Number(label.replace("%", ""));
}

test("«Код Java», «Консоль» и «Результат тестов» стоят рядом на экране 1440 пикселей", async ({ page, request }) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");

  for (const name of ["Код Java", "Консоль", "Результат тестов"]) {
    await expect(page.getByRole("region", { name })).toBeVisible();
  }
  await expect(page.getByRole("tab", { name: "Консоль" })).toHaveCount(0);

  const boxes = await Promise.all(
    ["Код Java", "Консоль", "Результат тестов"].map(async (name) => (await page.getByRole("region", { name }).boundingBox())!),
  );
  // в одной строке и слева направо
  expect(Math.abs(boxes[0].y - boxes[1].y)).toBeLessThan(2);
  expect(Math.abs(boxes[1].y - boxes[2].y)).toBeLessThan(2);
  expect(boxes[0].x).toBeLessThan(boxes[1].x);
  expect(boxes[1].x).toBeLessThan(boxes[2].x);
});

test("на узком экране нижние панели остаются вкладками", async ({ page, request }) => {
  await page.setViewportSize({ width: 1100, height: 800 });
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
  await expect(page.getByRole("tab", { name: "Консоль" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Консоль" })).toHaveCount(0);
});

test("переключатель темы меняет оформление, схему и редактор и помнится после перезагрузки", async ({ page, request }) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
  const html = page.locator("html");
  const canvas = page.locator(".react-flow");
  const editor = page.locator(".monaco-editor").first();

  await expect(html).toHaveClass(/dark/);
  await expect(canvas).toHaveClass(/dark/);
  await expect(editor).toHaveClass(/vs-dark/);

  await page.getByTestId("theme-toggle").click();
  await expect(html).toHaveClass(/light/);
  await expect(html).not.toHaveClass(/dark/);
  await expect(canvas).toHaveClass(/light/);
  await expect(editor).not.toHaveClass(/vs-dark/);

  await page.reload();
  await expect(page.getByTestId("workspace")).toBeVisible();
  await expect(html).toHaveClass(/light/);

  await page.getByTestId("theme-toggle").click();
  await expect(html).toHaveClass(/dark/);
});

test("крупная схема открывается в читаемом масштабе с началом метода, «Вписать» показывает её целиком", async ({
  page,
  request,
}) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.processOrders(List<Order>)");
  await expect.poll(() => blocks(page).count()).toBeGreaterThan(5);

  await expect(page.getByTestId("partial-view")).toBeVisible();
  await expect.poll(() => zoomPercent(page)).toBeGreaterThanOrEqual(85);
  // начало метода на экране: заголовок «Метод» виден целиком в пределах холста
  const canvasBox = (await page.getByTestId("flow-canvas").boundingBox())!;
  const header = (await page.locator('[data-testid^="block-method"]').first().boundingBox())!;
  expect(header.y).toBeGreaterThanOrEqual(canvasBox.y);
  expect(header.y + header.height).toBeLessThan(canvasBox.y + canvasBox.height);

  await page.getByRole("button", { name: "Вписать в окно" }).click();
  await expect(page.getByTestId("partial-view")).toHaveCount(0);
  await expect.poll(() => zoomPercent(page)).toBeLessThan(85);
});

test("небольшая схема по-прежнему вписывается целиком", async ({ page, request }) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
  await expect.poll(() => blocks(page).count()).toBeGreaterThan(2);
  await expect(page.getByTestId("partial-view")).toHaveCount(0);
});
