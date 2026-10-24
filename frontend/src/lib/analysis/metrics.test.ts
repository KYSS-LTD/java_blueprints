import { describe, expect, it } from "vitest";

import metricsFixture from "@/mocks/fixtures/metrics.json";
import type { components } from "@/lib/api/schema";

import { formatMetric, hardestMethods, metricRows } from "./metrics";

type Metrics = components["schemas"]["Metrics"];

const current = metricsFixture.metrics as unknown as Metrics;

function withTotals(patch: Partial<Metrics["totals"]>, methods = current.methods): Metrics {
  return { ...current, totals: { ...current.totals, ...patch }, methods };
}

describe("metricRows", () => {
  it("без версии сравнения «до» пусто, оценки нет", () => {
    const rows = metricRows(current, null);
    expect(rows.map((r) => r.key)).toEqual([
      "codeLines",
      "methods",
      "types",
      "cyclomaticAvg",
      "cyclomaticMax",
      "nesting",
      "duplication",
      "lcom4",
    ]);
    expect(rows.every((r) => r.before === null && r.improved === null)).toBe(true);
    expect(rows.find((r) => r.key === "codeLines")?.after).toBe(current.totals.code_lines);
  });

  it("считает вложенность как максимум по методам", () => {
    const deep = { ...current.methods[0], max_nesting: 9 };
    const rows = metricRows(withTotals({}, [...current.methods, deep]), null);
    expect(rows.find((r) => r.key === "nesting")?.after).toBe(9);
  });

  it("оценивает изменение: меньше цикломатики и дублей — лучше, размер не оценивается", () => {
    const before = withTotals({ cyclomatic_max: 30, duplication_percent: 4, code_lines: 900 });
    const after = withTotals({ cyclomatic_max: 22, duplication_percent: 6.5, code_lines: 700 });
    const rows = Object.fromEntries(metricRows(after, before).map((r) => [r.key, r]));
    expect(rows.cyclomaticMax).toMatchObject({ before: 30, after: 22, improved: true });
    expect(rows.duplication).toMatchObject({ before: 4, after: 6.5, improved: false });
    expect(rows.codeLines.improved).toBeNull();
    expect(rows.methods.improved).toBeNull();
  });

  it("одинаковые значения не считаются изменением", () => {
    const rows = metricRows(current, current);
    expect(rows.every((r) => r.improved === null)).toBe(true);
  });
});

describe("hardestMethods", () => {
  it("сортирует по цикломатике, затем по длине, и обрезает список", () => {
    const top = hardestMethods(current, 3);
    expect(top).toHaveLength(3);
    expect(top[0].cyclomatic).toBe(current.totals.cyclomatic_max);
    expect(top[0].cyclomatic).toBeGreaterThanOrEqual(top[1].cyclomatic);
    expect(top[1].cyclomatic).toBeGreaterThanOrEqual(top[2].cyclomatic);
  });

  it("не меняет исходный порядок методов", () => {
    const before = current.methods.map((m) => m.method_id);
    hardestMethods(current);
    expect(current.methods.map((m) => m.method_id)).toEqual(before);
  });
});

describe("formatMetric", () => {
  it.each([
    [22, "22"],
    [2.81, "2.81"],
    [6.5, "6.5"],
    [1.5, "1.5"],
    [0, "0"],
  ])("%s → %s", (value, text) => {
    expect(formatMetric(value)).toBe(text);
  });
});
