import type { components } from "@/lib/api/schema";

type RunInfo = components["schemas"]["RunInfo"];

/** Исходы условия: сколько раз оно оказалось истинным и ложным. */
export type Branch = { true: number; false: number };

/** Результат запуска `trace`, свёрнутый для наложения на блок-схемы («Дебаг», docs/07 § 6.2). */
export type DebugTrace = {
  runId: number;
  versionId: number | null;
  /** Основной счётчик блока: выполнения оператора, вычисления условия, итерации тела цикла. */
  hits: Record<string, number>;
  /** Входы в цикл (только у циклов). */
  entries: Record<string, number>;
  branches: Record<string, Branch>;
  /** Блоки, которые не удалось инструментировать: они не входят в покрытие и не серые. */
  untracked: Record<string, string>;
  coverage: number;
  covered: number;
  total: number;
  /** Счётчики достигли лимита и дальше не росли. */
  saturated: boolean;
  /** Сколько сценариев (или один запуск со своим вводом) дали эти счётчики. */
  runs: number;
  max: number;
};

type Details = { entries?: Record<string, number>; branches?: Record<string, Branch>; saturated?: boolean };

function asRecord<T>(value: unknown): Record<string, T> {
  return value && typeof value === "object" ? (value as Record<string, T>) : {};
}

/** Складывает счётчики элементов запуска: для набора сценариев — суммарные числа выполнений. */
export function buildTrace(info: RunInfo): DebugTrace | null {
  const summary = info.summary;
  const data = asRecord<unknown>(summary.data);
  const hits: Record<string, number> = {};
  for (const item of info.items) {
    for (const [id, count] of Object.entries(item.hits ?? {})) hits[id] = (hits[id] ?? 0) + count;
  }
  if (Object.keys(hits).length === 0) return null;

  const entries: Record<string, number> = {};
  const branches: Record<string, Branch> = {};
  let saturated = false;
  for (const detail of Object.values(asRecord<Details>(data.details))) {
    for (const [id, count] of Object.entries(detail.entries ?? {})) entries[id] = (entries[id] ?? 0) + count;
    for (const [id, branch] of Object.entries(detail.branches ?? {})) {
      const sum = branches[id] ?? { true: 0, false: 0 };
      branches[id] = { true: sum.true + branch.true, false: sum.false + branch.false };
    }
    saturated ||= Boolean(detail.saturated);
  }
  return {
    runId: info.id,
    versionId: info.version_id ?? null,
    hits,
    entries,
    branches,
    untracked: asRecord<string>(data.untracked),
    coverage: summary.coverage ?? 0,
    covered: Number(data.covered ?? 0),
    total: Number(data.total ?? Object.keys(hits).length),
    saturated,
    runs: info.items.length,
    max: Math.max(1, ...Object.values(hits)),
  };
}

/** Насыщенность цвета 0…1: логарифмическая шкала, чтобы редкие блоки не терялись рядом с горячим циклом. */
export function heat(count: number, max: number): number {
  if (count <= 0 || max <= 0) return 0;
  return Math.min(1, Math.log1p(count) / Math.log1p(max));
}

/** `12345` → `12К`, `2500000` → `2,5М`: счётчик должен помещаться в чип блока. */
export function formatCount(count: number): string {
  if (count < 10_000) return String(count);
  const compact = (value: number, suffix: string) =>
    `${value.toFixed(value < 100 ? 1 : 0).replace(".0", "").replace(".", ",")}${suffix}`;
  return count < 1_000_000 ? compact(count / 1000, "К") : compact(count / 1_000_000, "М");
}

/** Как блок выглядит на схеме: не инструментирован, не выполнялся или выполнялся (с насыщенностью). */
export type BlockHeat =
  | { state: "none" }
  | { state: "idle" }
  | { state: "hit"; count: number; heat: number; entries?: number; branch?: Branch };

export function blockHeat(trace: DebugTrace | null, id: string): BlockHeat {
  if (!trace || id in trace.untracked || !(id in trace.hits)) return { state: "none" };
  const count = trace.hits[id];
  const entries = trace.entries[id];
  const reached = entries !== undefined ? entries > 0 : count > 0;
  if (!reached) return { state: "idle" };
  return { state: "hit", count, heat: heat(count, trace.max), entries, branch: trace.branches[id] };
}
