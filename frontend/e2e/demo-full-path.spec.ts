import { expect, test } from "@playwright/test";

import { blocks, createProject, openWorkspace, runAndWait, sourceOf } from "./helpers";

// A4: полный путь на демо-монолите без ручных обходов: метод → схема → выделение → правка в инспекторе →
// код обновился → запуск → тесты зелёные.
test("demo_full_path: схема, выделение, правка условия, запуск, тесты", async ({ page, request }) => {
  const project = await createProject(request);
  expect((await runAndWait(request, project.id, { kind: "baseline" })).status).toBe("done");
  const original = await sourceOf(request, project.id);

  await openWorkspace(page, project.id, "Main.java#Main.discountFor(Order)");
  await expect.poll(() => blocks(page).count()).toBeGreaterThanOrEqual(5);

  // клик по блоку выделяет его: адрес получает node=, редактор показывает строки
  const condition = page.locator('[data-testid^="block-condition"]').first();
  await condition.click();
  await expect(page).toHaveURL(/node=/);

  // правка условия в инспекторе → код обновился
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("node-inspector")).toBeVisible();
  const field = page.getByTestId("node-inspector").locator("input, textarea").first();
  await expect(field).toHaveValue(/VIP_CUSTOMER/);
  await field.fill("(order.getCustomer() == VIP_CUSTOMER)"); // скобки: поведение не меняется
  await page.getByTestId("inspector-apply").click();
  await expect
    .poll(() => sourceOf(request, project.id))
    .toContain("if ((order.getCustomer() == VIP_CUSTOMER))");
  expect(await sourceOf(request, project.id)).not.toBe(original);

  // запуск: кириллица в выводе
  await page.getByTestId("run-button").click();
  await page.getByRole("button", { name: "Запустить", exact: true }).click();
  await expect(page.getByTestId("console-output")).toContainText("Система учёта заказов", { timeout: 60_000 });

  // тесты: правка поведение не меняет
  await page.getByTestId("run-tests").click();
  await expect(page.getByTestId("tests-message")).toContainText("Все тесты пройдены — 11 из 11", {
    timeout: 120_000,
  });
});
