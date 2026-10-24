import { describe, expect, it } from "vitest";

import type { Scenario } from "@/lib/api/actions";

import { buildRows, diffHeadline, hasTextDiff, summarize, type TestItem } from "./tests-view";

function scenario(id: number, name: string): Scenario {
  return {
    id,
    name,
    description: "",
    stdin: "",
    args: [],
    source: "manual",
    size_factor: null,
    enabled: true,
    stability: "stable",
    masks: [],
    baseline: { state: "recorded", version_id: 1, duration_ms: 10 },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function item(
  id: number | null,
  name: string,
  verdict: TestItem["verdict"],
  diff: TestItem["diff"] = null,
): TestItem {
  return { scenario_id: id, name, verdict, duration_ms: 5, baseline_ms: 4, diff };
}

describe("tests-view", () => {
  it("сопоставляет результаты со сценариями по id, а без id — по имени", () => {
    const rows = buildRows(
      [scenario(1, "one"), scenario(2, "two"), scenario(3, "three")],
      [item(1, "one", "PASS"), item(null, "two", "FAIL")],
    );
    expect(rows.map((r) => r.item?.verdict ?? null)).toEqual(["PASS", "FAIL", null]);
  });

  it("считает пройденные и расходящиеся", () => {
    expect(
      summarize([item(1, "a", "PASS"), item(2, "b", "FAIL"), item(3, "c", "TIMEOUT")]),
    ).toEqual({
      passed: 1,
      total: 3,
      failed: 2,
    });
  });

  it("коротко описывает расхождение", () => {
    expect(
      diffHeadline(
        item(1, "a", "FAIL", {
          stdout: { expected: "1", actual: "2", unified: "" },
          stderr: null,
          exit_code: null,
          exception: null,
        }),
      ),
    ).toBe("вывод отличается");
    expect(
      diffHeadline(
        item(1, "a", "FAIL", {
          stdout: null,
          stderr: null,
          exit_code: { expected: 0, actual: 1 },
          exception: null,
        }),
      ),
    ).toBe("код выхода 0 → 1");
    expect(
      diffHeadline(
        item(1, "a", "ERROR", {
          stdout: null,
          stderr: null,
          exit_code: null,
          exception: { expected: null, actual: "java.lang.Error: x" },
        }),
      ),
    ).toBe("java.lang.Error: x");
    expect(diffHeadline(item(1, "a", "PASS"))).toBeNull();
    expect(hasTextDiff(item(1, "a", "PASS"))).toBe(false);
  });
});
