"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChartLine } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { GrowthChart } from "@/components/workspace/growth-chart";
import { formatK, formatSpeedup, type Measured, readOptimize } from "@/lib/analysis/view";
import { projectQuery, runsQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";

const t = ru.analysis;
const tp = t.performance;

/**
 * Последнее измеренное ускорение: итог последней завершённой оптимизации, если она закончилась на
 * текущей версии (после отката или правки прежнее измерение к программе уже не относится).
 */
export function useMeasured(projectId: number): Measured | null {
  const project = useQuery(projectQuery(projectId));
  const runs = useQuery(runsQuery(projectId));
  const head = project.data?.head_version_id;
  return useMemo(() => {
    let latest: { id: number; measured: Measured } | null = null;
    for (const run of runs.data?.runs ?? []) {
      if (run.kind !== "optimize" || run.status !== "done") continue;
      const report = readOptimize(run.summary);
      if (!report?.measured || report.headVersionId !== head) continue;
      if (latest === null || run.id > latest.id) latest = { id: run.id, measured: report.measured };
    }
    return latest?.measured ?? null;
  }, [runs.data, head]);
}

export function SpeedupLine({ measured }: { measured: Measured | null }) {
  if (!measured?.speedup) {
    return (
      <span data-testid="speedup" data-measured="false">
        <span className="text-muted-foreground">{tp.notMeasured}</span>
        {measured?.error && <span className="text-muted-foreground"> · {measured.error}</span>}
      </span>
    );
  }
  return (
    <span data-testid="speedup" data-measured="true">
      <b className="text-node-return">
        {tp.upTo} {formatSpeedup(measured.speedup)}
      </b>
      {measured.size !== null && (
        <span className="text-muted-foreground">
          {" "}
          {tp.at} {measured.size}
          {measured.basis && `, ${tp.basis[measured.basis]}`}
        </span>
      )}
    </span>
  );
}

/** График «до/после» измеренной оптимизации и показатель роста k. */
export function MeasuredChart({ measured }: { measured: Measured }) {
  return (
    <div className="space-y-2 text-xs">
      <GrowthChart
        compared={measured.samples}
        metric={
          measured.basis === "time" ? "time_ms" : measured.basis === "hits" ? "hits" : undefined
        }
      />
      <p className="flex items-center gap-1.5">
        {tp.k}: <code className="rounded bg-muted px-1.5 py-0.5">{formatK(measured.beforeK)}</code>
        <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
        <code className="rounded bg-node-return/15 px-1.5 py-0.5 text-node-return">
          {formatK(measured.afterK)}
        </code>
      </p>
    </div>
  );
}

/** Нижний блок «Оценка производительности» панели «Анализ». */
export function PerformanceBlock({ projectId }: { projectId: number }) {
  const measured = useMeasured(projectId);
  const [chartOpen, setChartOpen] = useState(false);
  const canChart = (measured?.samples.length ?? 0) > 1;
  return (
    <div className="space-y-0.5 text-xs" aria-label={tp.title}>
      <p className="flex flex-wrap items-center gap-x-2">
        <span className="text-muted-foreground">{tp.speedup}:</span>
        <SpeedupLine measured={measured} />
        {measured && canChart && (
          <Button
            variant="link"
            size="xs"
            className="h-auto p-0 text-xs"
            onClick={() => setChartOpen(true)}
            data-testid="speedup-chart"
          >
            <ChartLine /> {tp.chart}
          </Button>
        )}
      </p>
      {!measured && <p className="text-[11px] text-muted-foreground">{tp.notMeasuredHint}</p>}
      {measured && (
        <Dialog open={chartOpen} onOpenChange={setChartOpen}>
          <DialogContent className="sm:max-w-xl">
            <DialogHeader>
              <DialogTitle>{tp.title}</DialogTitle>
              <DialogDescription>{tp.chart}</DialogDescription>
            </DialogHeader>
            <MeasuredChart measured={measured} />
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
