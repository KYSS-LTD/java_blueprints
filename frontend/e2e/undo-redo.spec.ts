import { expect, test } from "@playwright/test";

import { blocks, createProject, openWorkspace, sourceOf } from "./helpers";

// FR-08: операция над графом → undo возвращает код как до → redo возвращает правку.
test("undo_redo: Ctrl+D на блоке, отмена и повтор", async ({ page, request }) => {
  const project = await createProject(request);
  const original = await sourceOf(request, project.id);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
  await expect.poll(() => blocks(page).count()).toBeGreaterThan(1);

  const operation = page.locator('[data-testid^="block-operation"]').first();
  await operation.click();
  await page.keyboard.press("Control+d");
  await expect.poll(() => sourceOf(request, project.id)).not.toBe(original);
  const edited = await sourceOf(request, project.id);

  await page.getByTestId("undo-button").click();
  await expect.poll(() => sourceOf(request, project.id)).toBe(original);

  await page.getByTestId("redo-button").click();
  await expect.poll(() => sourceOf(request, project.id)).toBe(edited);
});
