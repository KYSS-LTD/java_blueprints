"use client";

import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import { ru } from "@/lib/i18n/ru";

const t = ru.benchmark;

export type GrowthSample = { size: number; time_ms: number; hits: number | null };

/** Точки «до» и «после» одного размера ввода (из `summary.data.measured.samples`). */
export type ComparedSample = { size: number; before: GrowthSample; after: GrowthSample };

type Point = { size: number; before?: number; after?: number; value?: number };

/** Что откладывать по оси Y: число выполнений блока, если оно измерено, иначе время. */
export function pickMetric(samples: GrowthSample[]): "hits" | "time_ms" {
  return samples.length > 0 && samples.every((s) => s.hits !== null && s.hits > 0)
    ? "hits"
    : "time_ms";
}

export function singlePoints(samples: GrowthSample[]): {
  points: Point[];
  metric: "hits" | "time_ms";
} {
  const metric = pickMetric(samples);
  return {
    metric,
    points: samples.map((s) => ({
      size: s.size,
      value: metric === "hits" ? (s.hits ?? 0) : s.time_ms,
    })),
  };
}

export function comparedPoints(
  samples: ComparedSample[],
  preferred?: "hits" | "time_ms",
): {
  points: Point[];
  metric: "hits" | "time_ms";
} {
  // ось задаёт вызывающий (ускорение посчитано по времени или по счётчикам); без него — по данным
  const metric = preferred ?? pickMetric(samples.flatMap((s) => [s.before, s.after]));
  const pick = (s: GrowthSample) => (metric === "hits" ? (s.hits ?? 0) : s.time_ms);
  return {
    metric,
    points: samples.map((s) => ({ size: s.size, before: pick(s.before), after: pick(s.after) })),
  };
}

const SINGLE: ChartConfig = { value: { label: t.chart, color: "var(--color-chart-1)" } };
const COMPARED: ChartConfig = {
  before: { label: t.before, color: "var(--color-chart-5)" },
  after: { label: t.after, color: "var(--color-chart-2)" },
};

/** График роста работы программы от размера ввода (shadcn Chart): одна линия или «до/после». */
export function GrowthChart({
  samples,
  compared,
  metric,
}: {
  samples?: GrowthSample[];
  compared?: ComparedSample[];
  /** Что откладывать по оси Y в режиме «до/после»; по умолчанию выбирается по данным. */
  metric?: "hits" | "time_ms";
}) {
  const view = compared ? comparedPoints(compared, metric) : singlePoints(samples ?? []);
  if (view.points.length < 2) return null;
  const config = compared ? COMPARED : SINGLE;
  return (
    <ChartContainer config={config} className="aspect-[16/9] w-full" data-testid="growth-chart">
      <LineChart data={view.points} margin={{ left: 4, right: 12, top: 8, bottom: 4 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="size" tickLine={false} axisLine={false} tickMargin={6} />
        <YAxis tickLine={false} axisLine={false} width={44} tickMargin={4} />
        <ChartTooltip content={<ChartTooltipContent />} />
        {compared && <ChartLegend content={<ChartLegendContent />} />}
        {(compared ? (["before", "after"] as const) : (["value"] as const)).map((key) => (
          <Line
            key={key}
            dataKey={key}
            type="monotone"
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            dot
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}
