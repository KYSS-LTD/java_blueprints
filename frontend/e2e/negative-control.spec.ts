import { expect, test } from "@playwright/test";

import { createProject, headOf, openWorkspace, runAndWait, sourceOf } from "./helpers";

// A7: внесённая ошибка ловится проверкой поведения — красный результат с diff, откат возвращает зелёный.
test("negative_control: `+` → `-` краснит тесты, undo возвращает зелёный", async ({ page, request }) => {
  const project = await createProject(request);
  expect((await runAndWait(request, project.id, { kind: "baseline" })).status).toBe("done");

  await openWorkspace(page, project.id);
  await expect(page.getByTestId("behavior-badge")).toBeVisible();

  // ошибка вносится правкой кода (как сделал бы пользователь): сумма заказа — `*` вместо `+`
  const original = await sourceOf(request, project.id);
  const broken = original.replace("return quantity * unitPrice;", "return quantity + unitPrice;");
  expect(broken).not.toBe(original);
  const head = await headOf(request, project.id);
  const saved = await request.put(`/api/projects/${project.id}/files/Main.java`, {
    data: { base_version_id: head, content: broken },
  });
  expect(saved.ok()).toBeTruthy();
  await page.reload();

  await page.getByTestId("run-tests").click();
  await expect(page.getByTestId("tests-message")).toContainText("Поведение отличается", { timeout: 120_000 });
  const failing = page.locator('[data-testid="test-row"][data-verdict="FAIL"]').first();
  await expect(failing).toBeVisible();
  await failing.getByRole("button").first().click();
  await expect(page.getByTestId("test-diff")).toBeVisible();

  // откат: undo возвращает исходный код, тесты снова зелёные
  await page.getByTestId("undo-button").click();
  await expect.poll(async () => sourceOf(request, project.id)).toBe(original);
  await page.getByTestId("run-tests").click();
  await expect(page.getByTestId("tests-message")).toContainText("Все тесты пройдены", { timeout: 120_000 });
});
