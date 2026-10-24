import { expect, test } from "@playwright/test";

import { createProject, openWorkspace, runAndWait } from "./helpers";

// FR-17, A6: вкладка «Декомпозиция» → план → применить → новые файлы, gate зелёный.
test("decompose: план на ≥ 3 класса и применение со стадиями gate", async ({ page, request }) => {
  const project = await createProject(request);
  expect((await runAndWait(request, project.id, { kind: "baseline" })).status).toBe("done");
  await openWorkspace(page, project.id);

  await page.getByTestId("open-decompose").click();
  await expect(page.getByTestId("decompose-view")).toBeVisible();
  await expect.poll(() => page.getByTestId("decompose-class").count()).toBeGreaterThanOrEqual(4); // ядро + ≥ 3 новых

  await page.getByTestId("decompose-apply").click();
  await expect(page.getByTestId("decompose-dialog")).toBeVisible();
  await page.getByTestId("decompose-confirm").click();
  await expect(page.getByTestId("decompose-outcome-message")).toHaveAttribute("data-candidate", "promoted", {
    timeout: 300_000,
  });

  const files = (await (await request.get(`/api/projects/${project.id}/files`)).json()) as {
    files: { path: string }[];
  };
  expect(files.files.length).toBeGreaterThanOrEqual(4);
});
