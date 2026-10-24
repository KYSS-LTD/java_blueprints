import { expect, test } from "@playwright/test";

import { createProject, openWorkspace, sourceOf } from "./helpers";

// S5: блок из палитры попадает в ветку switch (раньше вставка шла только в тело метода, циклы, if и try).
test("switch_insert: выбранная ветка case принимает блок из палитры перед break", async ({ page, request }) => {
  const project = await createProject(request);
  const before = await sourceOf(request, project.id);
  await openWorkspace(page, project.id, "Main.java#Main.processCommand(String, Scanner)");

  // первая ветка — case "add": addOrder(...); break;
  await page.getByTestId("container-case").first().click({ position: { x: 8, y: 8 } });
  await page.getByTestId("palette-operation").click();
  await page.getByLabel("Выражение").fill("commandCount += 0");
  await page.getByRole("button", { name: "Вставить" }).click();

  await expect.poll(() => sourceOf(request, project.id)).not.toBe(before);
  const after = await sourceOf(request, project.id);
  const start = after.indexOf('case "add":');
  const branch = after.slice(start, after.indexOf("break;", start));
  expect(branch).toContain("commandCount += 0;");
  expect(branch.indexOf("addOrder(")).toBeLessThan(branch.indexOf("commandCount += 0;"));
  // остальное не тронуто: добавилась ровно одна строка
  expect(after.split("\n").length).toBe(before.split("\n").length + 1);
});
