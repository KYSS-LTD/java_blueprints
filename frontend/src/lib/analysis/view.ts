import type { components } from "@/lib/api/schema";
import type { SourceRange } from "@/lib/graph/flow-model";
import type { RunSummary } from "@/lib/run/run-state";

type Schemas = components["schemas"];

export type Finding = Schemas["Finding"];
export type Severity = Finding["severity"];
export type Category = Finding["category"];
export type DynamicEvidence = Schemas["DynamicEvidence"];

export const SEVERITIES: Severity[] = ["high", "medium", "low"];
export const CATEGORIES: Category[] = ["PERF", "BUG", "MAINT"];

export type FindingFilters = {
  severities: ReadonlySet<Severity>;
  categories: ReadonlySet<Category>;
  /** Только находки этого файла; `null` — всех. */
  file: string | null;
};

export const NO_FILTERS: FindingFilters = {
  severities: new Set(SEVERITIES),
  categories: new Set(CATEGORIES),
  file: null,
};

export function filterFindings(findings: Finding[], filters: FindingFilters): Finding[] {
  return findings.filter(
    (f) =>
      filters.severities.has(f.severity) &&
      filters.categories.has(f.category) &&
      (filters.file === null || f.location.file === filters.file),
  );
}

export function filesOf(findings: Finding[]): string[] {
  return [...new Set(findings.map((f) => f.location.file))].sort();
}

/** Находки, для которых есть готовое исправление. */
export function fixableOf(findings: Finding[]): Finding[] {
  return findings.filter((f) => f.fix?.available);
}

/** Переключает значение в наборе; возвращает новый набор. */
export function toggled<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

// --- «Проверить» ----------------------------------------------------------------------------------------

export type GrowthSample = { size: number; time_ms: number; hits: number | null };

/** Итог динамической проверки находки: из запуска `verify` или из сохранённого `evidence.dynamic`. */
export type VerifyResult = {
  message: string | null;
  samples: GrowthSample[];
  growthK: number | null;
  /** Сложность, которую предсказал статический анализ. */
  predicted: string | null;
  warnings: string[];
  /** BUG/MAINT: в скольких сценариях выполнено место находки. */
  reachedBy: number | null;
  scenarios: number | null;
};

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function samplesOf(value: unknown): GrowthSample[] {
  if (!Array.isArray(value)) return [];
  const result: GrowthSample[] = [];
  for (const raw of value) {
    if (typeof raw !== "object" || raw === null) continue;
    const item = raw as Record<string, unknown>;
    const size = numberOrNull(item.size);
    const time = numberOrNull(item.time_ms);
    if (size === null || time === null) continue;
    result.push({ size, time_ms: time, hits: numberOrNull(item.hits) });
  }
  return result;
}

function stringsOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

export function readVerify(summary: RunSummary | null): VerifyResult | null {
  if (!summary) return null;
  const data = summary.data ?? {};
  return {
    message: summary.message,
    samples: samplesOf(data.samples),
    growthK: numberOrNull(data.growth_k),
    predicted: typeof data.predicted === "string" ? data.predicted : null,
    warnings: stringsOf(data.warnings),
    reachedBy: numberOrNull(data.reached_by),
    scenarios: numberOrNull(data.scenarios),
  };
}

export function verifyFromEvidence(
  dynamic: DynamicEvidence,
  predicted: string | null,
): VerifyResult {
  return {
    message: dynamic.verdict,
    samples: samplesOf(dynamic.samples),
    growthK: dynamic.growth_k,
    predicted,
    warnings: [],
    reachedBy: null,
    scenarios: null,
  };
}

/** Показатель роста для подписи: `1.97`. */
export function formatK(k: number | null): string {
  return k === null ? "—" : k.toFixed(2);
}

// --- измеренное ускорение ------------------------------------------------------------------------------

export type MeasuredSample = { size: number; before: GrowthSample; after: GrowthSample };

export type Measured = {
  speedup: number | null;
  size: number | null;
  basis: "time" | "hits" | null;
  beforeK: number | null;
  afterK: number | null;
  samples: MeasuredSample[];
  /** Почему измерить не удалось (ввод нечем масштабировать, ничего не применено). */
  error: string | null;
};

function sampleOf(value: unknown): GrowthSample | null {
  return samplesOf([value])[0] ?? null;
}

/** `summary.data.measured` запуска `optimize` (docs/plan/s8, S8.7). */
export function readMeasured(data: Record<string, unknown> | null | undefined): Measured | null {
  const raw = data?.measured;
  if (typeof raw !== "object" || raw === null) return null;
  const measured = raw as Record<string, unknown>;
  const samples: MeasuredSample[] = [];
  if (Array.isArray(measured.samples)) {
    for (const entry of measured.samples) {
      if (typeof entry !== "object" || entry === null) continue;
      const pair = entry as Record<string, unknown>;
      const before = sampleOf(pair.before);
      const after = sampleOf(pair.after);
      const size = numberOrNull(pair.size);
      if (before && after && size !== null) samples.push({ size, before, after });
    }
  }
  const basis = measured.basis === "time" || measured.basis === "hits" ? measured.basis : null;
  return {
    speedup: numberOrNull(measured.speedup),
    size: numberOrNull(measured.size),
    basis,
    beforeK: numberOrNull(measured.before_k),
    afterK: numberOrNull(measured.after_k),
    samples,
    error: typeof measured.error === "string" ? measured.error : null,
  };
}

/** `5.2×`: до десятых, без хвоста нулей у целых. */
export function formatSpeedup(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}×`;
}

// --- судьба кандидата после проверки --------------------------------------------------------------------

export type CandidateState = "promoted" | "rejected" | "candidate" | "stale";

/** Что стало с версией-кандидатом после gate (`summary.data.candidate`). */
export function candidateState(summary: RunSummary | null): CandidateState | null {
  const value = summary?.data?.candidate;
  return value === "promoted" || value === "rejected" || value === "candidate" || value === "stale"
    ? value
    : null;
}

export type OptimizeReport = {
  applied: string[];
  skipped: Record<string, string>;
  headVersionId: number | null;
  complexityAfter: string | null;
  measured: Measured | null;
};

export function readOptimize(summary: RunSummary | null): OptimizeReport | null {
  if (!summary) return null;
  const data = summary.data ?? {};
  const skipped: Record<string, string> = {};
  if (typeof data.skipped === "object" && data.skipped !== null) {
    for (const [key, value] of Object.entries(data.skipped)) {
      if (typeof value === "string") skipped[key] = value;
    }
  }
  return {
    applied: stringsOf(data.applied),
    skipped,
    headVersionId: numberOrNull(data.head_version_id),
    complexityAfter: typeof data.complexity_after === "string" ? data.complexity_after : null,
    measured: readMeasured(data),
  };
}

// --- маркеры в редакторе ------------------------------------------------------------------------------

/** Оценку сложности добавляем в подпись, если заголовок находки её ещё не содержит. */
function showComplexity(f: Finding): boolean {
  return f.complexity !== null && !f.title.includes(f.complexity.current);
}

export type FindingMarker = {
  severity: "warning" | "info";
  message: string;
  range: SourceRange;
};

/**
 * Волнистая линия под первой строкой каждой находки файла. Важные находки — предупреждения,
 * низкие — подсказки; в сообщении правило, заголовок и оценка сложности.
 */
export function findingMarkers(findings: Finding[], file: string | null): FindingMarker[] {
  if (file === null) return [];
  return findings
    .filter((f) => f.location.file === file)
    .map((f) => ({
      severity: f.severity === "low" ? ("info" as const) : ("warning" as const),
      message: `${f.rule_id}: ${f.title}${showComplexity(f) ? ` · ${f.complexity?.current}` : ""}`,
      range: {
        file,
        start_line: f.location.start_line,
        start_col: 1,
        end_line: f.location.start_line,
        end_col: 10_000,
      },
    }));
}

/**
 * Читаемое место находки по её id: `PERF-03:Main.java#Main.buildCsv()/foreach[0]` → `buildCsv()`.
 * У находок с номером строки (`…discountFor(Order):615`) номер отбрасывается.
 */
export function placeOfFindingId(id: string): string | null {
  const node = id.slice(id.indexOf(":") + 1);
  const hash = node.indexOf("#");
  if (hash === -1) return null;
  const method = node
    .slice(hash + 1)
    .split("/")[0]
    .replace(/:\d+$/, "");
  const open = method.indexOf("(");
  const dot = method.lastIndexOf(".", open === -1 ? undefined : open);
  return dot === -1 ? method : method.slice(dot + 1);
}
