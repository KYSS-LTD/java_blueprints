import { describe, expect, it } from "vitest";

import findings from "@/mocks/fixtures/findings.json";

import {
  CATEGORIES,
  type Finding,
  NO_FILTERS,
  SEVERITIES,
  candidateState,
  filesOf,
  filterFindings,
  placeOfFindingId,
  findingMarkers,
  fixableOf,
  formatK,
  formatSpeedup,
  readMeasured,
  readOptimize,
  readVerify,
  toggled,
  verifyFromEvidence,
} from "./view";

const all = findings.findings as unknown as Finding[];

function summary(data: Record<string, unknown> | null, message: string | null = null) {
  return { passed: null, total: null, coverage: null, version_status: null, message, data };
}

describe("фильтры находок", () => {
  it("без ограничений возвращают все находки", () => {
    expect(filterFindings(all, NO_FILTERS)).toHaveLength(all.length);
  });

  it("отбирают по важности, категории и файлу вместе", () => {
    const high = filterFindings(all, { ...NO_FILTERS, severities: new Set(["high"]) });
    expect(high.length).toBeGreaterThan(0);
    expect(high.every((f) => f.severity === "high")).toBe(true);

    const perf = filterFindings(all, { ...NO_FILTERS, categories: new Set(["PERF"]) });
    expect(perf.every((f) => f.category === "PERF")).toBe(true);

    const none = filterFindings(all, { ...NO_FILTERS, file: "Other.java" });
    expect(none).toEqual([]);
    expect(filterFindings(all, { ...NO_FILTERS, file: "Main.java" })).toHaveLength(all.length);
  });

  it("пустой набор важности скрывает всё", () => {
    expect(filterFindings(all, { ...NO_FILTERS, severities: new Set() })).toEqual([]);
  });

  it("файлы перечисляются без повторов по алфавиту", () => {
    expect(filesOf(all)).toEqual(["Main.java"]);
    expect(SEVERITIES).toEqual(["high", "medium", "low"]);
    expect(CATEGORIES).toEqual(["PERF", "BUG", "MAINT"]);
  });

  it("toggled не меняет исходный набор", () => {
    const start = new Set(["a"]);
    expect([...toggled(start, "b")].sort()).toEqual(["a", "b"]);
    expect([...toggled(start, "a")]).toEqual([]);
    expect([...start]).toEqual(["a"]);
  });

  it("рекомендации — находки с доступным исправлением", () => {
    const fixable = fixableOf(all);
    expect(fixable.length).toBeGreaterThan(0);
    expect(fixable.every((f) => f.fix?.available)).toBe(true);
  });
});

describe("итог «Проверить»", () => {
  it("разбирает рост, оценку и предупреждения запуска verify", () => {
    const result = readVerify(
      summary(
        {
          samples: [
            { size: 1000, time_ms: 12.5, hits: 1_000_000 },
            { size: 2000, time_ms: 51, hits: 4_000_000 },
            { size: "bad" },
          ],
          growth_k: 1.97,
          predicted: "O(n²)",
          warnings: ["мало точек", 5],
        },
        "~O(n²)",
      ),
    );
    expect(result).toMatchObject({
      message: "~O(n²)",
      growthK: 1.97,
      predicted: "O(n²)",
      warnings: ["мало точек"],
      reachedBy: null,
    });
    expect(result?.samples).toEqual([
      { size: 1000, time_ms: 12.5, hits: 1_000_000 },
      { size: 2000, time_ms: 51, hits: 4_000_000 },
    ]);
  });

  it("у BUG/MAINT берёт достижимость места сценариями", () => {
    const result = readVerify(
      summary({ reached_by: 3, scenarios: 11 }, "Место находки выполняется"),
    );
    expect(result).toMatchObject({ reachedBy: 3, scenarios: 11, growthK: null, samples: [] });
  });

  it("без итога результата нет, а сохранённые доказательства читаются так же", () => {
    expect(readVerify(null)).toBeNull();
    const stored = verifyFromEvidence(
      {
        run_id: 4,
        samples: [{ size: 1, time_ms: 2, hits: null }],
        growth_k: 1.1,
        verdict: "~O(n)",
      },
      "O(n)",
    );
    expect(stored).toMatchObject({ message: "~O(n)", growthK: 1.1, predicted: "O(n)" });
    expect(stored.samples).toHaveLength(1);
  });

  it("показатель роста и ускорение форматируются коротко", () => {
    expect(formatK(1.9712)).toBe("1.97");
    expect(formatK(null)).toBe("—");
    expect(formatSpeedup(5.24)).toBe("5.2×");
    expect(formatSpeedup(3)).toBe("3×");
    expect(formatSpeedup(2.96)).toBe("3×");
  });
});

describe("итог оптимизации", () => {
  const measured = {
    speedup: 5.2,
    size: 8000,
    basis: "time",
    before_k: 1.98,
    after_k: 1.02,
    samples: [
      {
        size: 4000,
        before: { size: 4000, time_ms: 80, hits: 16_000_000 },
        after: { size: 4000, time_ms: 20, hits: 4_000 },
      },
      {
        size: 8000,
        before: { size: 8000, time_ms: 320, hits: 64_000_000 },
        after: { size: 8000, time_ms: 40, hits: 8_000 },
      },
    ],
  };

  it("разбирает измеренное ускорение и точки до/после", () => {
    const report = readOptimize(
      summary({
        measured,
        applied: ["a", "b"],
        skipped: { c: "находка пропала", d: 3 },
        head_version_id: 12,
        complexity_after: "O(n)",
      }),
    );
    expect(report).toMatchObject({
      applied: ["a", "b"],
      skipped: { c: "находка пропала" },
      headVersionId: 12,
      complexityAfter: "O(n)",
    });
    expect(report?.measured).toMatchObject({
      speedup: 5.2,
      size: 8000,
      basis: "time",
      beforeK: 1.98,
      afterK: 1.02,
      error: null,
    });
    expect(report?.measured?.samples).toHaveLength(2);
  });

  it("если измерить нельзя, причина сохраняется", () => {
    const parsed = readMeasured({ measured: { error: "ввод нечем масштабировать" } });
    expect(parsed).toMatchObject({
      speedup: null,
      samples: [],
      error: "ввод нечем масштабировать",
    });
    expect(readMeasured({})).toBeNull();
    expect(readMeasured(null)).toBeNull();
  });
});

describe("судьба кандидата", () => {
  it.each(["promoted", "rejected", "candidate", "stale"] as const)("распознаёт %s", (state) => {
    expect(candidateState(summary({ candidate: state }))).toBe(state);
  });

  it("неизвестное значение и отсутствие итога дают null", () => {
    expect(candidateState(summary({ candidate: "weird" }))).toBeNull();
    expect(candidateState(summary(null))).toBeNull();
    expect(candidateState(null)).toBeNull();
  });
});

describe("маркеры находок в редакторе", () => {
  it("ставятся на первую строку находок открытого файла", () => {
    const markers = findingMarkers(all, "Main.java");
    expect(markers).toHaveLength(all.length);
    const first = markers[0];
    expect(first.range.start_line).toBe(first.range.end_line);
    expect(first.message).toMatch(/^[A-Z]+-\d+: /);
  });

  it("оценку сложности добавляет, только если заголовок её не содержит", () => {
    const markers = findingMarkers(all, "Main.java");
    const nested = markers[all.findIndex((f) => f.rule_id === "PERF-01")];
    expect(nested.message.match(/O\(n·m\)/g)).toHaveLength(1);
    const withoutInTitle = all.findIndex(
      (f) => f.complexity !== null && !f.title.includes(f.complexity.current),
    );
    if (withoutInTitle !== -1) {
      expect(markers[withoutInTitle].message).toContain(
        ` · ${all[withoutInTitle].complexity?.current}`,
      );
    }
  });

  it("низкая важность — подсказка, остальное — предупреждение", () => {
    const markers = findingMarkers(all, "Main.java");
    for (const [index, finding] of all.entries()) {
      expect(markers[index].severity).toBe(finding.severity === "low" ? "info" : "warning");
    }
  });

  it("для другого файла и без файла маркеров нет", () => {
    expect(findingMarkers(all, "Other.java")).toEqual([]);
    expect(findingMarkers(all, null)).toEqual([]);
  });
});

describe("место находки по id", () => {
  it.each([
    ["PERF-03:Main.java#Main.buildCsv()/foreach[0]", "buildCsv()"],
    ["PERF-02:Main.java#Main.repeat(char, int)/for[1]/if[0]", "repeat(char, int)"],
    ["BUG-01:Main.java#Main.discountFor(Order):615", "discountFor(Order)"],
    ["MAINT-05:Main.java#Main", "Main"],
  ])("%s → %s", (id, place) => {
    expect(placeOfFindingId(id)).toBe(place);
  });

  it("без пути к члену места нет", () => {
    expect(placeOfFindingId("PERF-01")).toBeNull();
  });
});
