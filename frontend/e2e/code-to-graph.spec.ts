import { expect, test } from "@playwright/test";

import { blocks, createProject, openWorkspace, sourceOf } from "./helpers";

// FR-07: правка кода в Monaco обновляет схему; результат сохраняется в версию.
test("code_to_graph: новая строка в Monaco появляется блоком на схеме", async ({ page, request }) => {
  const project = await createProject(request);
  await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
  await expect.poll(() => blocks(page).count()).toBeGreaterThan(1);
  const before = await blocks(page).count();
  await expect(page.locator(".monaco-editor").first()).toBeVisible();

  // строка вставляется командой редактора (как набор текста): сразу после заголовка метода
  const original = await sourceOf(request, project.id);
  const header = original.split("\n").findIndex((line) => line.includes("void printHeader()")) + 1;
  await page.evaluate((line) => {
    type Monaco = {
      editor: { getEditors(): { executeEdits(source: string, edits: unknown[]): void }[] };
      Range: new (a: number, b: number, c: number, d: number) => unknown;
    };
    const monaco = (window as unknown as { monaco: Monaco }).monaco;
    const editor = monaco.editor.getEditors()[0];
    editor.executeEdits("e2e", [
      { range: new monaco.Range(line + 1, 1, line + 1, 1), text: '        System.out.println("e2e");\n' },
    ]);
  }, header);

  await expect.poll(() => blocks(page).count(), { timeout: 5_000 }).toBeGreaterThan(before);
  await expect.poll(() => sourceOf(request, project.id), { timeout: 15_000 }).toContain('System.out.println("e2e");');
});
