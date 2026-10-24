import { describe, expect, it } from "vitest";

import { blockHeat, buildTrace, formatCount, heat } from "./trace";

const info = (items: Array<Record<string, number>>, data: Record<string, unknown> = {}) =>
  ({
    id: 7,
    kind: "trace",
    status: "done",
    version_id: 3,
    summary: { coverage: 0.5, data: { covered: 2, total: 4, ...data } },
    items: items.map((hits, i) => ({ name: `s${i}`, verdict: "PASS", duration_ms: 1, hits })),
  }) as never;

describe("buildTrace", () => {
  it("складывает счётчики сценариев и берёт покрытие из итога", () => {
    const trace = buildTrace(info([{ a: 1, b: 0 }, { a: 4, c: 2 }]));
    expect(trace).toMatchObject({
      runId: 7,
      versionId: 3,
      hits: { a: 5, b: 0, c: 2 },
      max: 5,
      runs: 2,
      coverage: 0.5,
      covered: 2,
      total: 4,
    });
  });

  it("суммирует входы в циклы и исходы условий по сценариям", () => {
    const trace = buildTrace(
      info([{ loop: 3 }, { loop: 2 }], {
        details: {
          "1": { entries: { loop: 1 }, branches: { cond: { true: 2, false: 1 } } },
          "2": { entries: { loop: 1 }, branches: { cond: { true: 0, false: 4 } }, saturated: true },
        },
      }),
    );
    expect(trace?.entries).toEqual({ loop: 2 });
    expect(trace?.branches).toEqual({ cond: { true: 2, false: 5 } });
    expect(trace?.saturated).toBe(true);
  });

  it("без счётчиков трассировки нет", () => {
    expect(buildTrace(info([{}]))).toBeNull();
  });
});

describe("blockHeat", () => {
  const trace = buildTrace(
    info([{ run: 3, skipped: 0, loop: 0, lam: 0 }], {
      untracked: { lam: "лямбда" },
      details: { "1": { entries: { loop: 2 } } },
    }),
  );

  it("различает выполненные, невыполненные и неинструментированные блоки", () => {
    expect(blockHeat(trace, "run")).toMatchObject({ state: "hit", count: 3 });
    expect(blockHeat(trace, "skipped")).toEqual({ state: "idle" });
    expect(blockHeat(trace, "lam")).toEqual({ state: "none" });
    expect(blockHeat(trace, "unknown")).toEqual({ state: "none" });
    expect(blockHeat(null, "run")).toEqual({ state: "none" });
  });

  it("цикл, в который вошли, но тело не выполнилось, считается достигнутым", () => {
    expect(blockHeat(trace, "loop")).toMatchObject({ state: "hit", count: 0, entries: 2 });
  });
});

describe("heat и formatCount", () => {
  it("шкала логарифмическая и ограничена единицей", () => {
    expect(heat(0, 100)).toBe(0);
    expect(heat(100, 100)).toBe(1);
    expect(heat(10, 100)).toBeGreaterThan(0.4);
    expect(heat(10, 100)).toBeLessThan(0.6);
  });

  it("сокращает большие числа", () => {
    expect(formatCount(42)).toBe("42");
    expect(formatCount(1_500)).toBe("1500");
    expect(formatCount(12_345)).toBe("12,3К");
    expect(formatCount(20_000)).toBe("20К");
    expect(formatCount(2_500_000)).toBe("2,5М");
  });
});
