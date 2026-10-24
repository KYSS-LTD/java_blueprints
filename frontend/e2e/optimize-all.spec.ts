import { expect, test } from "@playwright/test";

import { createProject, headOf, openWorkspace, runAndWait } from "./helpers";

// FR-16: «Оптимизировать» → план → применение по шагам с gate → отчёт «до/после».
test("optimize_all: план, применение, отчёт", async ({ page, request }) => {
  const project = await createProject(request);
  expect((await runAndWait(request, project.id, { kind: "baseline" })).status).toBe("done");
  const before = await headOf(request, project.id);
  await openWorkspace(page, project.id);

  await page.getByTestId("optimize-button").click();
  await expect(page.getByTestId("optimize-dialog")).toBeVisible();
  await expect.poll(() => page.getByTestId("optimize-step").count()).toBeGreaterThan(0);
  await page.getByTestId("optimize-start").click();

  const report = page.getByTestId("optimize-report");
  await expect(report).toBeVisible({ timeout: 300_000 });
  await expect(report).toContainText("Применено шагов");
  await expect.poll(() => headOf(request, project.id)).not.toBe(before);
});
