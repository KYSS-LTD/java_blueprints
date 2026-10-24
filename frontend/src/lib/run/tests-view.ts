import type { Scenario } from "@/lib/api/actions";
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

export type Verdict = Schemas["ItemData"]["verdict"];
export type TestItem = Pick<
  Schemas["ItemData"],
  "scenario_id" | "name" | "verdict" | "duration_ms" | "baseline_ms" | "diff"
>;

export type TestRow = {
  scenario: Scenario;
  /** Результат последнего прогона для сценария; `null` — в этом прогоне его не было. */
  item: TestItem | null;
};

/** Сценарии вместе с результатами последнего прогона (по `scenario_id`, а при его отсутствии — по имени). */
export function buildRows(scenarios: Scenario[], items: TestItem[]): TestRow[] {
  const byId = new Map<number, TestItem>();
  const byName = new Map<string, TestItem>();
  for (const item of items) {
    if (item.scenario_id !== null) byId.set(item.scenario_id, item);
    byName.set(item.name, item);
  }
  return scenarios.map((scenario) => ({
    scenario,
    item: byId.get(scenario.id) ?? byName.get(scenario.name) ?? null,
  }));
}

export type TestSummary = { passed: number; total: number; failed: number };

export function summarize(items: TestItem[]): TestSummary {
  const passed = items.filter((i) => i.verdict === "PASS").length;
  return { passed, total: items.length, failed: items.length - passed };
}

/** Сценарии, расхождение которых можно показать построчно. */
export function hasTextDiff(item: TestItem | null): boolean {
  return item?.diff?.stdout != null || item?.diff?.stderr != null;
}

/** Короткое описание расхождения для строки таблицы. */
export function diffHeadline(item: TestItem | null): string | null {
  const diff = item?.diff;
  if (!diff) return null;
  if (diff.exception) return diff.exception.actual ?? "исключение";
  if (diff.exit_code) return `код выхода ${diff.exit_code.expected} → ${diff.exit_code.actual}`;
  if (diff.stdout) return "вывод отличается";
  if (diff.stderr) return "поток ошибок отличается";
  return null;
}
