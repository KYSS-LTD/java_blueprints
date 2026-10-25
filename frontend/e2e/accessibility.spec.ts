import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { blocks, createProject, openWorkspace, runAndWait } from "./helpers";

// S10.3: автоматическая проверка доступности (WCAG 2.1 A/AA) основных экранов в светлой и тёмной теме.
// Тема выбирается так же, как переключателем в шапке: значение `theme` в localStorage (next-themes); эмуляция
// `prefers-color-scheme` на неё не влияет, поэтому до появления переключателя оба прогона шли в тёмной теме.
// Критичные и серьёзные нарушения недопустимы; остальные выводятся в лог, чтобы их было видно при разборе.
async function audit(page: Page, name: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  // по каждому нарушению — первые узлы (а не один): иначе исправление идёт по одному узлу за прогон
  const lines = result.violations.flatMap((v) =>
    v.nodes.slice(0, 6).map(
      (node) =>
        `${v.impact}: ${v.id} (${v.nodes.length}) ${node.target.join(" ")}
      ${node.html.slice(0, 160)}
      ${node.any[0]?.message ?? ""}`,
    ),
  );
  console.log(
    `a11y ${name}: ${lines.length === 0 ? "нарушений нет" : "\n  " + lines.join("\n  ")}`,
  );
  const blocking = result.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious",
  );
  expect(
    blocking.map((v) => v.id),
    `${name}: критичные и серьёзные нарушения`,
  ).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`тема: ${scheme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((value) => window.localStorage.setItem("theme", value), scheme);
    });

    test("a11y: экран проектов", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("heading").first()).toBeVisible();
      await expect(page.locator("html")).toHaveClass(new RegExp(scheme));
      await audit(page, `проекты, ${scheme}`);
    });

    test("a11y: рабочая область, находки, рекомендации и тесты", async ({ page, request }) => {
      const project = await createProject(request);
      expect((await runAndWait(request, project.id, { kind: "baseline" })).status).toBe("done");
      await openWorkspace(page, project.id, "Main.java#Main.printHeader()");
      await expect.poll(() => blocks(page).count()).toBeGreaterThan(1);
      await expect(page.locator("html")).toHaveClass(new RegExp(scheme));
      await audit(page, `рабочая область, ${scheme}`);

      await expect(page.getByTestId("finding-card").first()).toBeVisible();
      await audit(page, `панель находок, ${scheme}`);

      await page.getByRole("tab", { name: /Рекомендации/ }).click();
      await expect(page.getByTestId("fix-row").first()).toBeVisible();
      await audit(page, `рекомендации, ${scheme}`);

      await page.getByTestId("optimize-button").click();
      await expect(page.getByTestId("optimize-dialog")).toBeVisible();
      await expect.poll(() => page.getByTestId("optimize-step").count()).toBeGreaterThan(0);
      await audit(page, `диалог «Оптимизировать», ${scheme}`);
      await page.keyboard.press("Escape");

      await page.getByTestId("open-decompose").click();
      await expect(page.getByTestId("decompose-view")).toBeVisible();
      await expect.poll(() => page.getByTestId("decompose-class").count()).toBeGreaterThan(1);
      await audit(page, `декомпозиция, ${scheme}`);
    });
  });
}
