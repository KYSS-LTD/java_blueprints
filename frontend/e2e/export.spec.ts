import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

import { createProject, openWorkspace } from "./helpers";

// FR-19: «Экспорт Java» отдаёт архив с исходниками (его компиляция `javac` проверяется в scripts/bug_bash.py).
test("export: архив исходников скачивается и содержит Main.java", async ({ page, request }) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id);

  await page.getByRole("button", { name: "Экспорт Java" }).click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: /Исходники/ }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  const path = await download.path();
  const bytes = await readFile(path);
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  expect(bytes.includes(Buffer.from("Main.java"))).toBe(true);
});
