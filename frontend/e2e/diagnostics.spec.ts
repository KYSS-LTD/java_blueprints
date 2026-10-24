import { expect, test } from "@playwright/test";

import { createProject, openWorkspace, runAndWait } from "./helpers";

async function complexityOf(request: import("@playwright/test").APIRequestContext, projectId: number) {
  const analysis = (await (await request.get(`/api/projects/${projectId}/analysis`)).json()) as {
    complexity: { current: string };
    summary: { total: number };
  };
  return analysis.complexity.current;
}

// FR-13, FR-16: диагноз → клик выделяет узел → предпросмотр исправления → «Применить» → gate зелёный.
test("diagnostics: находка PERF-01, предпросмотр и применение исправления", async ({ page, request }) => {
  const project = await createProject(request);
  expect((await runAndWait(request, project.id, { kind: "baseline" })).status).toBe("done"); // без эталона gate нечего сравнивать
  await openWorkspace(page, project.id);

  const finding = page.getByTestId("finding-card").filter({ hasText: "PERF-01" }).first();
  await expect(finding).toBeVisible();
  await finding.getByRole("button").first().click();
  await expect(page).toHaveURL(/node=/);
  await expect(page.getByRole("tab", { name: /mergeDuplicates/ })).toBeVisible();

  const before = await complexityOf(request, project.id);

  await page.getByRole("tab", { name: /Рекомендации/ }).click();
  const row = page.getByTestId("fix-row").filter({ hasText: "OPT-02" }).first();
  await expect(row).toBeVisible();
  await row.getByTestId("fix-preview").click();

  const dialog = page.getByTestId("fix-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId("file-diffs")).toBeVisible();
  await page.getByTestId("fix-apply").click();
  await expect(page.getByTestId("fix-outcome-message")).toHaveAttribute("data-candidate", "promoted", {
    timeout: 180_000,
  });

  // исправление вошло в HEAD: проект проверен, находка PERF-02 исчезла
  const after = (await (await request.get(`/api/projects/${project.id}/analysis`)).json()) as {
    findings: { rule_id: string; location: { method_id: string } }[];
    complexity: { current: string };
  };
  expect(after.findings.some((f) => f.rule_id === "PERF-02" && f.location.method_id.includes("distinctCustomers"))).toBe(false);
  expect(before).toBeTruthy();
});
