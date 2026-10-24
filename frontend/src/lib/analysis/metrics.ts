import type { components } from "@/lib/api/schema";

type Metrics = components["schemas"]["Metrics"];
type MethodMetrics = components["schemas"]["MethodMetrics"];

export type MetricKey =
  | "codeLines"
  | "methods"
  | "types"
  | "cyclomaticAvg"
  | "cyclomaticMax"
  | "nesting"
  | "duplication"
  | "lcom4";

export type MetricRow = {
  key: MetricKey;
  /** Значение «до» (оригинал); `null` — сравнивать не с чем. */
  before: number | null;
  after: number;
  /** `true` — стало лучше, `false` — хуже, `null` — не оценивается или без изменений. */
  improved: boolean | null;
};

/** Больше — хуже у всех показателей, кроме размера (число методов и типов само по себе не плохо). */
const NEUTRAL: ReadonlySet<MetricKey> = new Set(["codeLines", "methods", "types"]);

function maxNesting(metrics: Metrics): number {
  return metrics.methods.reduce((max, m) => Math.max(max, m.max_nesting), 0);
}

function valuesOf(metrics: Metrics): Record<MetricKey, number> {
  const { totals } = metrics;
  return {
    codeLines: totals.code_lines,
    methods: totals.methods,
    types: totals.types,
    cyclomaticAvg: totals.cyclomatic_avg,
    cyclomaticMax: totals.cyclomatic_max,
    nesting: maxNesting(metrics),
    duplication: totals.duplication_percent,
    lcom4: totals.lcom4_avg,
  };
}

const ORDER: MetricKey[] = [
  "codeLines",
  "methods",
  "types",
  "cyclomaticAvg",
  "cyclomaticMax",
  "nesting",
  "duplication",
  "lcom4",
];

/** Строки таблицы «Оригинал → Текущая». Без версии сравнения `before` пуст. */
export function metricRows(current: Metrics, before: Metrics | null | undefined): MetricRow[] {
  const now = valuesOf(current);
  const was = before ? valuesOf(before) : null;
  return ORDER.map((key) => {
    const a = now[key];
    const b = was ? was[key] : null;
    const changed = b !== null && Math.abs(a - b) > 0.005;
    return {
      key,
      before: b,
      after: a,
      improved: changed && !NEUTRAL.has(key) ? a < (b as number) : null,
    };
  });
}

/** Методы по убыванию цикломатической сложности (при равенстве — по длине). */
export function hardestMethods(metrics: Metrics, limit = 8): MethodMetrics[] {
  return [...metrics.methods]
    .sort(
      (a, b) => b.cyclomatic - a.cyclomatic || b.lines - a.lines || a.name.localeCompare(b.name),
    )
    .slice(0, limit);
}

/** Число без хвоста нулей: `2.81`, `22`, `6.5`. */
export function formatMetric(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}
